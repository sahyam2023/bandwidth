from flask import Flask, request, jsonify, render_template, g # Added g for context
import datetime
import time
import threading
import ipaddress
# Removed deque as history is in DB now
import copy
import sys
import sqlite3 
import json     
from dateutil import parser as dateutil_parser
import socket

# --- Configuration ---
LISTEN_IP = "0.0.0.0"
LISTEN_PORT = 8000
STALE_THRESHOLD_SECONDS = 120
# HISTORY_LENGTH removed - history is now in DB
NETWORK_CHOKE_THRESHOLD_PERCENT = 80.0
DATABASE = 'collector_data.db' # <-- NEW: Database file path
try:
     COLLECTOR_HOSTNAME = socket.gethostname()
     # Optionally resolve hostname to an IP if needed, but hostname is better ID
     # COLLECTOR_IP_FOR_GRAPH = socket.gethostbyname(COLLECTOR_HOSTNAME) # Might fail/be ambiguous
     COLLECTOR_ID_FOR_GRAPH = COLLECTOR_HOSTNAME # Use hostname as the primary ID/Name
except socket.gaierror:
     COLLECTOR_ID_FOR_GRAPH = LISTEN_IP # Fallback (0.0.0.0 isn't ideal, maybe use a specific interface IP?)
     COLLECTOR_HOSTNAME = "Collector" # Fallback name

print(f"--- Collector Identifier for Graph: {COLLECTOR_ID_FOR_GRAPH} (Name: {COLLECTOR_HOSTNAME}) ---")

# --- Alerting Thresholds (Example) ---
ALERT_CPU_THRESHOLD = 90.0
ALERT_MEM_THRESHOLD = 90.0
ALERT_DISK_THRESHOLD = 95.0 # Check against individual disk 'percent'
ALERT_AGENT_DOWN_SECONDS = STALE_THRESHOLD_SECONDS * 2 # When to consider agent down


# --- Data Cleanup Configuration ---
DATA_RETENTION_DAYS = 7  # <---- CHANGED FROM 60 to 7
CLEANUP_INTERVAL_HOURS = 24
CLEANUP_BATCH_SIZE = 5000
MAX_HISTORY_POINTS_QUERY = 2000

#ping

ALERT_PING_FAIL_THRESHOLD_MINUTES = 5 # Trigger alert if ping fails for this many minutes
ALERT_PING_LATENCY_THRESHOLD_MS = 500


# --- Global Variables ---
# Removed agent_data_store and data_lock for history
# NEW: In-memory snapshot for *latest* data only + Active Alerts
latest_agent_snapshot = {} # { hostname: { last_seen: ts, latest_metrics: {...} } }
active_alerts = {}         # { alert_id: { hostname, type, message, value, threshold, start_time } }
# Use separate locks for snapshot and alerts
snapshot_lock = threading.Lock()
alerts_lock = threading.Lock()

app = Flask(__name__)

# --- Database Functions ---
def get_db():
    """Opens a new database connection if there is none yet for the current application context."""
    db = getattr(g, '_database', None)
    if db is None:
        try:
            db = g._database = sqlite3.connect(DATABASE, timeout=10) # Added timeout
            # Use Row factory for dict-like access
            db.row_factory = sqlite3.Row
            # Enable Write-Ahead Logging for better concurrency
            db.execute("PRAGMA journal_mode=WAL;")
        except sqlite3.Error as e:
            print(f"!!! DATABASE CONNECTION ERROR: {e}")
            return None # Propagate error
    return db

@app.teardown_appcontext
def close_connection(exception):
    """Closes the database again at the end of the request."""
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    """Initializes the database and creates tables if they don't exist."""
    print(f"Initializing database at: {DATABASE}")
    try:
        # Use a context manager for the connection in init
        with sqlite3.connect(DATABASE, timeout=10) as conn:
            cursor = conn.cursor()
            print("Creating 'agents' table (if not exists)...")
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS agents (
                    hostname TEXT PRIMARY KEY,
                    agent_ip TEXT,
                    first_seen REAL,
                    last_seen REAL,
                    tags TEXT
                )
            ''')
            print("Creating 'metrics' table (if not exists)...")
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    hostname TEXT,
                    timestamp_utc TEXT,
                    timestamp_unix REAL,
                    interval_sec REAL,
                    cpu_percent REAL,
                    mem_percent REAL,
                    disk_usage TEXT, -- JSON blob
                    disk_io TEXT, -- JSON blob
                    network_total_sent_mbps REAL,
                    network_total_recv_mbps REAL,
                    network_interfaces TEXT, -- JSON blob
                    peer_traffic TEXT, -- JSON blob
                    FOREIGN KEY (hostname) REFERENCES agents (hostname)
                        ON DELETE CASCADE -- Optional: delete metrics if agent is deleted
                )
            ''')
            print("Creating 'alerts' table (if not exists)...")
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alert_key TEXT UNIQUE NOT NULL, -- Composite key (hostname + type + maybe specific disk/iface)
                    hostname TEXT NOT NULL,
                    alert_type TEXT NOT NULL, -- e.g., 'cpu_high', 'mem_high', 'disk_high', 'net_choked', 'agent_down'
                    specific_target TEXT, -- Optional: e.g., Disk name ('C') or Interface name ('Ethernet')
                    status TEXT NOT NULL, -- 'active', 'resolved' (maybe 'acknowledged' later)
                    message TEXT,
                    current_value REAL, -- Store the value that triggered/updated the alert
                    threshold_value REAL, -- Store the threshold that was breached
                    first_triggered_unix REAL NOT NULL, -- Timestamp when first became active
                    last_active_unix REAL, -- Timestamp when last seen as active
                    resolved_unix REAL, -- Timestamp when status changed to 'resolved'
                    FOREIGN KEY (hostname) REFERENCES agents (hostname) ON DELETE CASCADE
                )
            ''')
            # Create indexes for faster lookups
            print("Creating indexes (if not exists)...")
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_metrics_hostname_timestamp ON metrics (hostname, timestamp_unix DESC)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_agents_last_seen ON agents (last_seen DESC)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_alerts_key ON alerts (alert_key)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_alerts_status_time ON alerts (status, last_active_unix DESC)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_alerts_hostname ON alerts (hostname)')

            conn.commit()
            print("Database initialized successfully.")
    except sqlite3.Error as e:
        print(f"!!! DATABASE INITIALIZATION FAILED: {e}")
        sys.exit(1) # Exit if DB init fails

# --- Helper Functions (Keep extract_key_metrics and format_time_ago as they are useful) ---
def extract_key_metrics(payload):
    # ... (Keep existing implementation - it processes one payload for the snapshot) ...
    # ... (Ensure it handles potential missing keys gracefully) ...
    # ADDED: Extract Disk IOPS for potential alerting/display
    disk_io_payload = payload.get('disk_io', {})
    processed_disk_io = {}
    if isinstance(disk_io_payload, dict):
         for disk_name, io_data in disk_io_payload.items():
              if isinstance(io_data, dict):
                  processed_disk_io[disk_name] = {
                      "read_ops_ps": io_data.get("read_ops_ps", -1.0),
                      "write_ops_ps": io_data.get("write_ops_ps", -1.0),
                      "read_Bps": io_data.get("read_Bps", -1.0),
                      "write_Bps": io_data.get("write_Bps", -1.0),
                  }
                 

    # Existing network payload processing
    network_payload = payload.get('network', {})
    ping_results_payload = payload.get('ping_results', {})
    processed_ping_results = {}
    if isinstance(ping_results_payload, dict):
        for target_ip, ping_data in ping_results_payload.items():
            if isinstance(ping_data, dict):
                 processed_ping_results[target_ip] = {
                     "status": ping_data.get("status", "unknown"),
                     "latency_ms": ping_data.get("latency_ms", None),
                     # Keep timestamp if needed for staleness check later
                     "timestamp": ping_data.get("timestamp", None),                 }
    metrics = {
        'hostname': payload.get('hostname', 'Unknown'),
        'agent_ip': payload.get('agent_ip', 'N/A'),
        'cpu_percent': payload.get('cpu', {}).get('percent', -1.0),
        'mem_percent': payload.get('memory', {}).get('percent', -1.0),
        'total_throughput_mbps': network_payload.get('total', {}).get('throughput_Mbps', -1.0),
        'sent_mbps': network_payload.get('total', {}).get('sent_Mbps', -1.0),
        'recv_mbps': network_payload.get('total', {}).get('recv_Mbps', -1.0),
        'total_nic_speed_mbps': network_payload.get('reported_total_link_speed_mbps', 0),
        'disks': {}, # Keep existing disk usage processing
        'disk_io': processed_disk_io, # ADDED processed disk IOPS/Bps
        'network_adapters': {}, # Processed NIC data (utilization, speed)
        'peer_traffic': payload.get('peer_traffic', {}),
        'timestamp_utc': payload.get('timestamp_utc', datetime.datetime.utcnow().isoformat() + "Z"),
        'ping_results': processed_ping_results
        # 'interval_sec' can be added if needed from payload.get('interval_sec')
    }
    # --- Disk Usage Processing --- (Keep as is)
    disk_usage_payload = payload.get('disk_usage', {})
    if isinstance(disk_usage_payload, dict):
        for disk_name, usage_data in disk_usage_payload.items():
             if isinstance(usage_data, dict):
                  metrics['disks'][disk_name] = {
                      'percent': usage_data.get('percent', -1.0),
                      'free_gb': usage_data.get('free_gb', -1.0),
                      'total_gb': usage_data.get('total_gb', -1.0)
                  }
    # --- Network Adapter Processing --- (Keep as is, checks choke)
    interfaces_payload = network_payload.get('interfaces', {})
    if isinstance(interfaces_payload, dict):
         # ... (keep existing logic for calculating utilization/choke status) ...
         for adapter_name, adapter_data in interfaces_payload.items():
             if isinstance(adapter_data, dict):
                  # Calculate utilization based on *sent/recv* percentages from agent
                  is_up = adapter_data.get('is_up', False)
                  sent_percent = adapter_data.get('sent_percent_of_link', -1.0)
                  recv_percent = adapter_data.get('recv_percent_of_link', -1.0)
                  valid_percents = [p for p in [sent_percent, recv_percent] if isinstance(p, (int, float)) and p >= 0]
                  utilization = max(valid_percents) if valid_percents else -1.0
                  is_choked = False
                  if is_up and utilization >= NETWORK_CHOKE_THRESHOLD_PERCENT:
                      is_choked = True

                  metrics['network_adapters'][adapter_name] = {
                      'is_up': is_up,
                      'utilization_percent': utilization,
                      'link_speed_mbps': adapter_data.get('link_speed_mbps', 0),
                      'sent_Mbps': adapter_data.get('sent_Mbps', -1.0),
                      'recv_Mbps': adapter_data.get('recv_Mbps', -1.0),
                      'is_choked': is_choked # Important for alerts
                  }
    return metrics

def generate_alert_key(hostname, alert_type, specific_target=None):
    key = f"{hostname}_{alert_type}"
    if specific_target:
        # Normalize target name slightly (replace spaces, common chars) for consistency
        safe_target = str(specific_target).replace(' ', '_').replace(':', '').replace('\\','').replace('/','')
        key += f"_{safe_target}"
    return key

def format_time_ago(seconds_past):
    # ... (Keep existing implementation) ...
    if not isinstance(seconds_past, (int, float)) or seconds_past < 0: return 'N/A'
    seconds_past = int(seconds_past) # Convert to int for display
    if seconds_past < 60: return f"{seconds_past}s ago"
    if seconds_past < 3600: return f"{seconds_past // 60}m ago"
    return f"{seconds_past // 3600}h ago"


# --- Data Cleanup Functions ---

def cleanup_old_metrics():
    """Deletes metric data older than DATA_RETENTION_DAYS using ROWID for compatibility."""
    retention_seconds = DATA_RETENTION_DAYS * 24 * 60 * 60
    cutoff_timestamp = time.time() - retention_seconds
    total_deleted_rows = 0
    print(f"[{datetime.datetime.now()}] Starting database cleanup: Deleting metrics older than {DATA_RETENTION_DAYS} days (before {datetime.datetime.fromtimestamp(cutoff_timestamp)})...")

    conn = None
    try:
        conn = sqlite3.connect(DATABASE, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL;")
        cursor = conn.cursor()

        while True:
            # Step 1: Select ROWIDs of the batch to delete
            cursor.execute(
                "SELECT rowid FROM metrics WHERE timestamp_unix < ? LIMIT ?",
                (cutoff_timestamp, CLEANUP_BATCH_SIZE)
            )
            rowids_to_delete = [row[0] for row in cursor.fetchall()] # Fetch all rowids in the batch

            if not rowids_to_delete:
                # No more rows older than the cutoff found
                break

            # Step 2: Delete the selected rows using their ROWIDs
            # Create placeholders for the IN clause: (?, ?, ...)
            placeholders = ','.join('?' * len(rowids_to_delete))
            cursor.execute(
                f"DELETE FROM metrics WHERE rowid IN ({placeholders})",
                rowids_to_delete
            )

            deleted_count = cursor.rowcount
            conn.commit() # Commit the batch delete
            total_deleted_rows += deleted_count

            print(f"  ...deleted {total_deleted_rows} rows so far...")
            # No sleep needed here usually, as the select/delete is fairly quick per batch

            # Safety break if something unexpected happens (shouldn't be needed with LIMIT)
            if deleted_count == 0 and len(rowids_to_delete)>0:
                 print("Warning: Deleted 0 rows despite selecting rowids. Stopping cleanup.")
                 break

    except sqlite3.Error as e:
        print(f"!!! DATABASE CLEANUP ERROR: {e}")
        if conn:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
    except Exception as e:
         print(f"!!! UNEXPECTED CLEANUP ERROR: {e}")
    finally:
        if conn:
            conn.close()

    print(f"[{datetime.datetime.now()}] Database cleanup finished. Attempted to delete {total_deleted_rows} metric rows.")
    # Optional VACUUM remains the same
    # ...

        # Optional: VACUUM to reclaim disk space (can be slow and locks DB)
        # if total_deleted_rows > 0:
        #     print(f"[{datetime.datetime.now()}] Starting VACUUM...")
        #     conn.execute("VACUUM;")
        #     conn.commit()
        #     print(f"[{datetime.datetime.now()}] VACUUM finished.")

def run_cleanup_scheduler():
    """Runs the cleanup task periodically."""
    print("Starting background cleanup scheduler...")
    # Optional: Add an initial delay before the first run
    # time.sleep(60 * 5) # Wait 5 minutes after startup

    while True:
        try:
            cleanup_old_metrics()
        except Exception as e:
             # Catch broad exceptions to prevent the scheduler thread from crashing
             print(f"!!! CRITICAL ERROR IN CLEANUP SCHEDULER LOOP: {e}")
             import traceback
             traceback.print_exc()

        # Sleep until the next scheduled run
        sleep_duration = CLEANUP_INTERVAL_HOURS * 60 * 60
        print(f"[{datetime.datetime.now()}] Next cleanup scheduled in {CLEANUP_INTERVAL_HOURS} hours.")
        time.sleep(sleep_duration)

# --- Alerting Functions ---
def check_and_update_alerts(hostname, latest_metrics):
    """Checks latest metrics against thresholds and updates alerts table in DB."""
    now = time.time()
    db = get_db() # Get DB connection from context
    if not db:
        print("!!! ERROR: Cannot check alerts, DB connection failed.")
        return # Cannot proceed without DB

    cursor = db.cursor()

    def update_or_insert_alert(is_triggered, alert_key, alert_type, message, value, threshold, specific_target=None):
        try:
            if is_triggered:
                # Alert condition is MET - Insert or Update (UPSERT)
                cursor.execute("""
                    INSERT INTO alerts (alert_key, hostname, alert_type, specific_target, status, message, current_value, threshold_value, first_triggered_unix, last_active_unix, resolved_unix)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                    ON CONFLICT(alert_key) DO UPDATE SET
                        status = 'active', -- Ensure status is active
                        message = excluded.message,
                        current_value = excluded.current_value,
                        threshold_value = excluded.threshold_value,
                        last_active_unix = excluded.last_active_unix,
                        resolved_unix = NULL -- Clear resolved time if it reactivates
                    WHERE alerts.status != 'active' OR -- Update if not active (e.g. resolved -> active)
                          alerts.current_value != excluded.current_value OR -- Update if value changed
                          alerts.message != excluded.message -- Update if message changed (e.g. threshold)
                """, (alert_key, hostname, alert_type, specific_target, 'active', message, value, threshold, now, now))
                if cursor.rowcount > 0:
                     print(f"ALERT DB: Inserted/Updated ACTIVE alert for {alert_key}")

            else:
                # Alert condition is NOT MET - Update status to 'resolved' if it was active
                cursor.execute("""
                    UPDATE alerts
                    SET status = 'resolved', resolved_unix = ?
                    WHERE alert_key = ? AND status = 'active'
                """, (now, alert_key))
                if cursor.rowcount > 0:
                     print(f"ALERT DB: Resolved alert for {alert_key}")

            # Always commit after each check/potential update
            db.commit()

        except sqlite3.Error as e:
            print(f"!!! DB ERROR updating alert for {alert_key}: {e}")
            db.rollback() # Rollback on error for this specific alert update


    # --- Check CPU ---
    cpu_key = generate_alert_key(hostname, 'cpu_high')
    cpu_percent = latest_metrics.get('cpu_percent', -1.0)
    cpu_triggered = isinstance(cpu_percent, (int, float)) and cpu_percent >= ALERT_CPU_THRESHOLD
    cpu_message = f"CPU usage {cpu_percent:.1f}% >= {ALERT_CPU_THRESHOLD}%" if cpu_triggered else "CPU usage OK"
    update_or_insert_alert(cpu_triggered, cpu_key, 'cpu_high', cpu_message, cpu_percent, ALERT_CPU_THRESHOLD)

    # --- Check Memory ---
    mem_key = generate_alert_key(hostname, 'mem_high')
    mem_percent = latest_metrics.get('mem_percent', -1.0)
    mem_triggered = isinstance(mem_percent, (int, float)) and mem_percent >= ALERT_MEM_THRESHOLD
    mem_message = f"Memory usage {mem_percent:.1f}% >= {ALERT_MEM_THRESHOLD}%" if mem_triggered else "Memory usage OK"
    update_or_insert_alert(mem_triggered, mem_key, 'mem_high', mem_message, mem_percent, ALERT_MEM_THRESHOLD)

    # --- Check Disk Usage ---
    disks = latest_metrics.get('disks', {})
    if isinstance(disks, dict):
        for disk_name, disk_data in disks.items():
            disk_key = generate_alert_key(hostname, 'disk_high', disk_name)
            disk_percent = disk_data.get('percent', -1.0)
            disk_triggered = isinstance(disk_percent, (int, float)) and disk_percent >= ALERT_DISK_THRESHOLD
            disk_message = f"Disk '{disk_name}' usage {disk_percent:.1f}% >= {ALERT_DISK_THRESHOLD}%" if disk_triggered else f"Disk '{disk_name}' OK"
            update_or_insert_alert(disk_triggered, disk_key, 'disk_high', disk_message, disk_percent, ALERT_DISK_THRESHOLD, specific_target=disk_name)

    # --- Check Network Choke ---
    adapters = latest_metrics.get('network_adapters', {})
    if isinstance(adapters, dict):
        for adapter_name, adapter_data in adapters.items():
             choke_key = generate_alert_key(hostname, 'net_choked', adapter_name)
             is_choked = adapter_data.get('is_choked', False)
             util_percent = adapter_data.get('utilization_percent', -1.0)
             choke_message = f"Adapter '{adapter_name}' choked (Util: {util_percent:.1f}% >= {NETWORK_CHOKE_THRESHOLD_PERCENT}%)" if is_choked else f"Adapter '{adapter_name}' OK"
             update_or_insert_alert(is_choked, choke_key, 'net_choked', choke_message, util_percent, NETWORK_CHOKE_THRESHOLD_PERCENT, specific_target=adapter_name)
    
    ping_results = latest_metrics.get('ping_results', {})
    if isinstance(ping_results, dict):
        # Calculate the cutoff time for persistent failures
        fail_cutoff_time = now - (ALERT_PING_FAIL_THRESHOLD_MINUTES * 60)

        for target_ip, ping_data in ping_results.items():
             if not isinstance(ping_data, dict): continue

             ping_status = ping_data.get("status", "unknown")
             latency = ping_data.get("latency_ms", None)
             ping_timestamp = ping_data.get("timestamp", None) # When this specific ping was done

             # --- A. Check for Ping Failure/Timeout ---
             ping_fail_key = generate_alert_key(hostname, 'ping_fail', target_ip)
             is_failing = ping_status in ['timeout', 'error']
             fail_message = f"Ping to {target_ip} failed (Status: {ping_status})" if is_failing else f"Ping to {target_ip} OK"
             fail_value = 1 if is_failing else 0 # Use 1 for fail, 0 for success
             fail_threshold = 1 # Trigger if value is 1 (i.e., is_failing is true)

             # This basic check triggers immediately on failure. Need persistence check.
             # We check if the *last* known status for this PING_FAIL_KEY alert in DB was 'active'
             # AND if its last_active_unix time is older than our fail_cutoff_time.
             # This requires querying the DB first.

             is_persistently_failing = False
             if is_failing:
                  try:
                      cursor.execute("""
                          SELECT last_active_unix FROM alerts
                          WHERE alert_key = ? AND status = 'active'
                      """, (ping_fail_key,))
                      result = cursor.fetchone()
                      # Check if it was active before and if that time is old enough
                      # Also consider if this *ping's* timestamp is recent enough
                      if result and ping_timestamp and ping_timestamp > fail_cutoff_time:
                           # If it was active previously, keep it active (update last_active_time)
                           # The UPSERT logic below handles this.
                           is_persistently_failing = True # Treat as still failing for UPSERT
                      elif ping_timestamp and ping_timestamp > fail_cutoff_time:
                           # This is potentially the *first* failure within the window,
                           # UPSERT will insert it as active. If it resolves next time, it gets cleared.
                           is_persistently_failing = True
                      # else: the ping failure is too old or no prior active alert, don't trigger yet


                  except sqlite3.Error as e:
                      print(f"!!! DB Error checking previous ping fail status for {ping_fail_key}: {e}")

             # Use the persistence check result for the trigger status
             update_or_insert_alert(is_persistently_failing, ping_fail_key, 'ping_fail', fail_message, fail_value, fail_threshold, specific_target=target_ip)


             # --- B. Optional: Check for High Ping Latency ---
             ping_latency_key = generate_alert_key(hostname, 'ping_latency_high', target_ip)
             latency_ok = latency is not None and isinstance(latency, (int, float))
             is_high_latency = latency_ok and latency >= ALERT_PING_LATENCY_THRESHOLD_MS
             latency_message = f"Ping latency to {target_ip} high ({latency:.1f}ms >= {ALERT_PING_LATENCY_THRESHOLD_MS}ms)" if is_high_latency else f"Ping latency to {target_ip} OK"

             # This triggers immediately on high latency. No persistence check added here for simplicity,
             # but could be added similarly to the failure check if needed.
             update_or_insert_alert(is_high_latency, ping_latency_key, 'ping_latency_high', latency_message, latency, ALERT_PING_LATENCY_THRESHOLD_MS, specific_target=target_ip)

    # --- END NEW Check Ping 

    # --- Resolve Agent Down Alert (if agent reported back) ---
    agent_down_key = generate_alert_key(hostname, 'agent_down')
    update_or_insert_alert(False, agent_down_key, 'agent_down', 'Agent reported back', None, None) # Force resolved status


def check_agent_down_status():
    """Background task to check for agents that haven't reported recently and update DB."""
    print("Starting background agent down check...")
    while True:
        time.sleep(STALE_THRESHOLD_SECONDS / 2) # Check periodically
        now = time.time()
        conn_bg = None # Use separate connection for background thread

        try:
            conn_bg = sqlite3.connect(DATABASE, timeout=10)
            conn_bg.execute("PRAGMA journal_mode=WAL;") # Good practice for concurrency
            cursor_bg = conn_bg.cursor()

            # Get all agents and their last seen time
            cursor_bg.execute("SELECT hostname, last_seen FROM agents")
            all_agents = cursor_bg.fetchall()

            active_agents_checked = set()

            for hostname, last_seen in all_agents:
                active_agents_checked.add(hostname) # Track hosts we checked
                agent_down_key = generate_alert_key(hostname, 'agent_down')
                is_down = False
                time_since_seen = None
                if last_seen:
                     time_since_seen = now - last_seen
                     is_down = time_since_seen > ALERT_AGENT_DOWN_SECONDS
                else:
                     # Agent exists but never seen? Consider it down? Or ignore? Let's ignore for now.
                     continue

                if is_down:
                    # Agent is DOWN - UPSERT alert
                    down_message = f"Agent has not reported in > {ALERT_AGENT_DOWN_SECONDS} seconds (last seen {time_since_seen:.0f}s ago)."
                    cursor_bg.execute("""
                        INSERT INTO alerts (alert_key, hostname, alert_type, status, message, current_value, threshold_value, first_triggered_unix, last_active_unix, resolved_unix)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                        ON CONFLICT(alert_key) DO UPDATE SET
                            status = 'active',
                            message = excluded.message,
                            current_value = excluded.current_value,
                            last_active_unix = excluded.last_active_unix,
                            resolved_unix = NULL -- Ensure resolved is null
                        WHERE alerts.status != 'active' OR -- Update if not active
                              alerts.current_value != excluded.current_value -- Update if time delta changed significantly (optional precision)
                    """, (agent_down_key, hostname, 'agent_down', 'active', down_message, time_since_seen, ALERT_AGENT_DOWN_SECONDS, now, now))
                    if cursor_bg.rowcount > 0:
                        print(f"ALERT DB: Inserted/Updated ACTIVE 'agent_down' for {hostname}")
                # else: # Agent is UP (handled by check_and_update_alerts when agent reports)
                    # No need to resolve here, resolve happens when data is received

            conn_bg.commit() # Commit changes for this batch of checks

        except sqlite3.Error as e:
            print(f"!!! DB ERROR in agent down check: {e}")
            if conn_bg: conn_bg.rollback()
        except Exception as e:
             print(f"!!! UNEXPECTED ERROR in agent down check: {e}")
             import traceback; traceback.print_exc()
        finally:
            if conn_bg: conn_bg.close()


# --- MODIFIED /data endpoint ---
@app.route('/data', methods=['POST'])
def receive_agent_data():
    global latest_agent_snapshot, snapshot_lock
    if not request.is_json: return jsonify({"error": "Request must be JSON"}), 400
    payload = request.get_json()
    if not payload or not isinstance(payload, dict): return jsonify({"error": "No valid JSON data received"}), 400

    hostname = payload.get('hostname')
    agent_ip = payload.get('agent_ip')
    # Basic validation
    if not hostname or not isinstance(hostname, str) or not hostname.strip():
         hostname = f"ip_{agent_ip or request.remote_addr}"
         payload['hostname'] = hostname
    if not agent_ip or not isinstance(agent_ip, str):
        agent_ip = request.remote_addr
        payload['agent_ip'] = agent_ip
    try:
        ipaddress.ip_address(agent_ip) # Validate IP format
    except ValueError:
         print(f"ERROR: Received invalid agent IP format '{agent_ip}' from remote {request.remote_addr}. Rejecting.")
         return jsonify({"error": f"Invalid agent_ip format: {agent_ip}"}), 400

    # --- Process and Store Data ---
    current_time_unix = time.time()
    utc_timestamp_str = payload.get('timestamp_utc', datetime.datetime.utcnow().isoformat() + "Z")
    interval = payload.get('interval_sec', -1.0)

    db = get_db()
    if not db:
        return jsonify({"error": "Database connection failed"}), 500

    cursor = db.cursor()

    try:
        # --- 1. Update Agent Info ---
        cursor.execute('''
            INSERT INTO agents (hostname, agent_ip, first_seen, last_seen)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(hostname) DO UPDATE SET
                agent_ip = excluded.agent_ip,
                last_seen = excluded.last_seen
            ''', (hostname, agent_ip, current_time_unix, current_time_unix))

        # --- 2. Extract and Serialize Metrics ---
        cpu = payload.get('cpu', {}).get('percent', None)
        mem = payload.get('memory', {}).get('percent', None)
        disk_usage_json = json.dumps(payload.get('disk_usage', {}))
        disk_io_json = json.dumps(payload.get('disk_io', {}))
        net_total = payload.get('network', {}).get('total', {})
        net_interfaces_json = json.dumps(payload.get('network', {}).get('interfaces', {}))
        peer_traffic_json = json.dumps(payload.get('peer_traffic', {}))

        # --- 3. Insert Metrics into DB ---
        cursor.execute('''
            INSERT INTO metrics (
                hostname, timestamp_utc, timestamp_unix, interval_sec,
                cpu_percent, mem_percent, disk_usage, disk_io,
                network_total_sent_mbps, network_total_recv_mbps,
                network_interfaces, peer_traffic
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                hostname, utc_timestamp_str, current_time_unix, interval,
                cpu, mem, disk_usage_json, disk_io_json,
                net_total.get('sent_Mbps', None), net_total.get('recv_Mbps', None),
                net_interfaces_json, peer_traffic_json
            ))

        db.commit()
        # print(f"Data received and stored for {hostname}") # Can be noisy

    except sqlite3.Error as e:
        db.rollback() # Rollback on error
        print(f"\n!!! DATABASE ERROR processing data for {hostname}: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": "Internal server error storing data"}), 500
    # No need to close connection here, Flask teardown context handles it.

    # --- 4. Update In-Memory Snapshot ---
    try:
        processed_metrics = extract_key_metrics(payload) # Process for snapshot/alerting
        with snapshot_lock:
            latest_agent_snapshot[hostname] = {
                "last_seen": current_time_unix,
                "latest_metrics": processed_metrics
            }
    except Exception as e:
         print(f"\nError updating snapshot for {hostname}: {e}")
         # Continue even if snapshot update fails, DB is primary

    # --- 5. Check Alerts ---
    try:
        check_and_update_alerts(hostname, processed_metrics)
    except Exception as e:
        print(f"\nError checking alerts for {hostname}: {e}")
        # Log error but don't fail the request

    sys.stdout.write('.'); sys.stdout.flush() # Indicate success
    return jsonify({"status": "success"}), 200


# --- MODIFIED /api/latest_data endpoint ---
@app.route('/api/latest_data')
def get_latest_data():
    global latest_agent_snapshot, snapshot_lock
    current_time = time.time()
    active_data = {}
    # Get snapshot under lock
    with snapshot_lock:
        # Create a copy to iterate over outside the lock
        snapshot_copy = copy.deepcopy(latest_agent_snapshot)
    
    db = get_db()
    if not db: return jsonify({"error": "Database connection failed"}), 500
    
    # --- Get current alerting hostnames under lock ---
    alerting_hostnames = set()
    try:
         cursor = db.cursor()
         # Select distinct hostnames that have at least one 'active' alert
         cursor.execute("SELECT DISTINCT hostname FROM alerts WHERE status = 'active'")
         rows = cursor.fetchall()
         alerting_hostnames = {row['hostname'] for row in rows}
    except sqlite3.Error as e:
         print(f"Warning: DB error fetching alerting hostnames for latest_data: {e}")
    # --- END Alerting Hostname Fetch ---

    db = get_db()
    if not db: return jsonify({"error": "Database connection failed"}), 500

    # Process hosts from the snapshot
    for hostname, agent_info in snapshot_copy.items():
        last_seen = agent_info.get('last_seen', 0)
        if (current_time - last_seen) <= STALE_THRESHOLD_SECONDS:
            agent_metrics = agent_info.get('latest_metrics', {})
            agent_metrics['last_seen_relative'] = format_time_ago(current_time - last_seen)
            agent_metrics['has_alert'] = hostname in alerting_hostnames

            # --- Fetch small history slice for Sparklines ---
            history_slice = {"cpu": [], "mem": []} # Add more if needed (e.g., network)
            try:
                cursor = db.cursor()
                # Fetch last N points (adjust N as needed, e.g., 15-20)
                cursor.execute('''
                    SELECT timestamp_unix, cpu_percent, mem_percent
                    FROM metrics
                    WHERE hostname = ?
                    ORDER BY timestamp_unix DESC
                    LIMIT 20
                ''', (hostname,))
                rows = cursor.fetchall()
                # Add in reverse order so oldest is first for charting
                for row in reversed(rows):
                    history_slice["cpu"].append(row["cpu_percent"])
                    history_slice["mem"].append(row["mem_percent"])

            except sqlite3.Error as e:
                print(f"Warning: DB error fetching history slice for {hostname}: {e}")
                # Continue without history slice if DB fails

            agent_metrics['history_slice'] = history_slice
            # --- End Sparkline Data Fetch ---

            active_data[hostname] = {
                "data": agent_metrics, # Contains latest metrics + history slice
                "last_seen": last_seen # Keep original last_seen if needed
            }

    return jsonify(active_data)


# --- REMOVED /api/host_history/<hostname> endpoint ---
# This will be re-added in Phase 3 with proper DB querying for ranges

# --- MODIFIED /api/get_peer_ips endpoint ---
@app.route('/api/get_peer_ips')
def get_peer_ips():
    active_ips = set()
    current_time = time.time()
    stale_limit_time = current_time - STALE_THRESHOLD_SECONDS

    db = get_db()
    if not db: return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = db.cursor()
        cursor.execute('SELECT agent_ip FROM agents WHERE last_seen >= ?', (stale_limit_time,))
        rows = cursor.fetchall()
        for row in rows:
            agent_ip = row['agent_ip']
            if agent_ip and agent_ip != 'N/A':
                try:
                    ipaddress.ip_address(agent_ip)
                    active_ips.add(agent_ip)
                except ValueError:
                    print(f"Warning: Invalid IP format '{agent_ip}' in agents table, skipping.")

    except sqlite3.Error as e:
        print(f"DB Error fetching peer IPs: {e}")
        return jsonify({"error": "Failed to query peer IPs"}), 500

    return jsonify(list(active_ips))

# --- MODIFIED /api/all_peer_flows endpoint ---
@app.route('/api/all_peer_flows')
def get_all_peer_flows():
    nodes_dict = {}
    links = []
    current_time = time.time()
    stale_limit_time = current_time - STALE_THRESHOLD_SECONDS

    db = get_db()
    if not db: return jsonify({"error": "Database connection failed"}), 500

    # --- NEW: Explicitly add Collector node ---
    # Use a unique ID. If collector runs on same IP as an agent, this ID needs to be distinct.
    collector_node_id = COLLECTOR_ID_FOR_GRAPH # Use configured ID
    nodes_dict[collector_node_id] = { # Use the ID as the key
         "id": collector_node_id,
         "name": COLLECTOR_HOSTNAME, # Display name
         "hostname": COLLECTOR_HOSTNAME, # Store hostname
         "is_collector": True # Mark as collector
    }
    # --- END Add Collector Node ---

    active_agents = {} # Store latest metric info {hostname: {ip: ..., peer_traffic: ...}}

    try:
        cursor = db.cursor()
        # Get active hostnames and their IPs first
        cursor.execute('SELECT hostname, agent_ip FROM agents WHERE last_seen >= ?', (stale_limit_time,))
        active_host_rows = cursor.fetchall()

        # No need to return early if no agents, collector node should still show
        if not active_host_rows:
            return jsonify({"nodes": list(nodes_dict.values()), "links": []}) # Return collector node

        host_list = [row['hostname'] for row in active_host_rows]
        ip_to_hostname_map = {row['agent_ip']: row['hostname'] for row in active_host_rows if row['agent_ip']}
        
        # Add agent nodes (ensure not overwriting collector if IDs clash)
        for row in active_host_rows:
            agent_ip = row['agent_ip']
            hostname = row['hostname']
            agent_node_id = agent_ip # Agent nodes ID'd by IP for linking
            
            # Add agent node if ID doesn't clash with collector ID
            if agent_node_id != collector_node_id and agent_node_id not in nodes_dict:
                nodes_dict[agent_node_id] = {"id": agent_node_id, "name": hostname, "hostname": hostname, "is_collector": False}
            elif agent_node_id == collector_node_id:
                print(f"Warning: Agent {hostname} IP ({agent_ip}) matches Collector ID ({collector_node_id}). Collector node takes precedence.")

        # Efficiently get the latest metric row ID for each active agent
        placeholders = ','.join('?' * len(host_list))
        cursor.execute(f'''
            SELECT hostname, MAX(timestamp_unix) as max_ts FROM metrics
            WHERE hostname IN ({placeholders}) GROUP BY hostname
        ''', host_list)
        latest_timestamps = {row['hostname']: row['max_ts'] for row in cursor.fetchall()}
        # Now fetch the actual peer_traffic JSON for those latest timestamps
        # Prepare (hostname, timestamp) pairs for the query
        query_params = []
        hosts_to_query_final = []
        for host, ts in latest_timestamps.items():
             # Ensure the latest metric isn't too old itself (double check staleness)
             if ts >= stale_limit_time:
                  hosts_to_query_final.append(host)
                  query_params.extend([host, ts])

        if not hosts_to_query_final:
             return jsonify({"nodes": list(nodes_dict.values()), "links": []})
         
        # Build query string like WHERE (hostname = ? AND timestamp_unix = ?) OR (...)
        where_clause = " OR ".join(["(hostname = ? AND timestamp_unix = ?)"] * len(hosts_to_query_final))
        cursor.execute(f'''
            SELECT hostname, peer_traffic
            FROM metrics
            WHERE {where_clause}
        ''', query_params)

        latest_metrics_rows = cursor.fetchall()

        # Process the fetched peer traffic data
        for row in latest_metrics_rows:
            peer_traffic_json = row['peer_traffic']
            if not peer_traffic_json: continue

            try:
                peer_traffic_data = json.loads(peer_traffic_json)
                if isinstance(peer_traffic_data, dict):
                    for flow_key, flow_data in peer_traffic_data.items():
                        if isinstance(flow_data, dict) and 'Mbps' in flow_data:
                            try:
                                source_ip, target_ip = flow_key.split('_to_')
                                rate_mbps = flow_data.get('Mbps', 0.0)
                                rate_mbps = round(rate_mbps, 3) if isinstance(rate_mbps, (float, int)) else 0.0

                                # Add source/target nodes if they don't exist and don't clash with collector
                                if source_ip != collector_node_id and source_ip not in nodes_dict:
                                    nodes_dict[source_ip] = {"id": source_ip, "name": ip_to_hostname_map.get(source_ip, source_ip), "hostname": ip_to_hostname_map.get(source_ip), "is_collector": False}
                                if target_ip != collector_node_id and target_ip not in nodes_dict:
                                    nodes_dict[target_ip] = {"id": target_ip, "name": ip_to_hostname_map.get(target_ip, target_ip), "hostname": ip_to_hostname_map.get(target_ip), "is_collector": False}

                                # Add link (ensure source/target IDs exist in our final dict)
                                if source_ip in nodes_dict and target_ip in nodes_dict:
                                    links.append({
                                        "source": source_ip,
                                        "target": target_ip,
                                        "rate_mbps": rate_mbps,
                                        "type": "peer_traffic" # Explicitly set type
                                    })
                            except (ValueError, KeyError, Exception):
                                continue # Skip invalid flows/IPs
            except (json.JSONDecodeError, TypeError) as e:
                 print(f"Warning: Could not parse peer_traffic JSON for {row['hostname']}: {e}")
                 continue

    except sqlite3.Error as e:
        print(f"DB Error fetching peer flows: {e}")
        return jsonify({"error": "Failed to query peer flows"}), 500
    except Exception as e:
        print(f"Unexpected error fetching peer flows: {e}")
        return jsonify({"error": "Unexpected error processing peer flows"}), 500

    # Prepare final graph data structure
    node_list = list(nodes_dict.values())
    graph_data = {"nodes": node_list, "links": links}
    return jsonify(graph_data)

# --- NEW /api/alerts endpoint ---
@app.route('/api/alerts')
def get_alerts():
    """Returns alerts, optionally filtered by status."""
    # Allow filtering by status (e.g., /api/alerts?status=active)
    status_filter = request.args.get('status', default='active', type=str).lower()

    valid_statuses = ['active', 'resolved', 'all']
    if status_filter not in valid_statuses:
        status_filter = 'active' # Default to active if invalid status provided

    db = get_db()
    if not db: return jsonify({"error": "Database connection failed"}), 500

    alerts_list = []
    try:
        cursor = db.cursor()
        sql = "SELECT hostname, alert_type, specific_target, status, message, current_value, threshold_value, first_triggered_unix, last_active_unix, resolved_unix FROM alerts"
        params = []

        if status_filter != 'all':
            sql += " WHERE status = ?"
            params.append(status_filter)

        # Optionally add ordering
        sql += " ORDER BY last_active_unix DESC, first_triggered_unix DESC"

        cursor.execute(sql, params)
        rows = cursor.fetchall()

        for row in rows:
            # Convert row object to dictionary for jsonify
            alerts_list.append(dict(row))

    except sqlite3.Error as e:
        print(f"DB Error fetching alerts: {e}")
        return jsonify({"error": "Failed to query alerts"}), 500

    return jsonify(alerts_list)

# --- NEW: /api/summary endpoint ---
@app.route('/api/summary')
def get_summary_stats():
    global latest_agent_snapshot, snapshot_lock, active_alerts, alerts_lock
    current_time = time.time()
    stale_limit_time = current_time - STALE_THRESHOLD_SECONDS

    summary = {
        "total_agents_active": 0,
        "agents_with_alerts": 0,
        "total_network_throughput_mbps": 0.0,
        "total_peer_traffic_mbps": 0.0,
    }

    db = get_db()
    if not db: return jsonify({"error": "Database connection failed"}), 500

    active_hostnames = set()
    latest_metrics_data = {} # Store latest network/peer traffic for aggregation

    try:
        cursor = db.cursor()

        # --- 1. Get Active Agents Count & Hostnames ---
        cursor.execute('SELECT hostname FROM agents WHERE last_seen >= ?', (stale_limit_time,))
        active_rows = cursor.fetchall()
        summary["total_agents_active"] = len(active_rows)
        active_hostnames = {row['hostname'] for row in active_rows}

        if not active_hostnames:
            return jsonify(summary) # Return early if no active agents

        # --- 2. Get Agents with Alerts ---
        # Check our in-memory active_alerts
        alerting_hostnames = set()
        with alerts_lock:
            for alert_id, alert_data in active_alerts.items():
                 # Only count active alerts and ensure the host is currently considered active
                 if alert_data.get("status") == "active" and alert_data.get("hostname") in active_hostnames:
                      # Exclude 'agent_down' alerts from this specific count if desired
                      # if alert_data.get("type") != "agent_down":
                      alerting_hostnames.add(alert_data.get("hostname"))
        summary["agents_with_alerts"] = len(alerting_hostnames)


        # --- 3. Aggregate Network and Peer Traffic from Latest Metrics ---
        # Reuse the logic from /api/all_peer_flows to get latest metrics efficiently
        placeholders = ','.join('?' * len(active_hostnames))
        cursor.execute(f'''
            SELECT hostname, MAX(timestamp_unix) as max_ts
            FROM metrics
            WHERE hostname IN ({placeholders})
            GROUP BY hostname
        ''', list(active_hostnames))
        latest_timestamps = {row['hostname']: row['max_ts'] for row in cursor.fetchall()}

        # Prepare (hostname, timestamp) pairs for the final query
        query_params = []
        hosts_to_query_final = []
        for host, ts in latest_timestamps.items():
             if ts >= stale_limit_time: # Ensure latest metric isn't stale
                  hosts_to_query_final.append(host)
                  query_params.extend([host, ts])

        if not hosts_to_query_final:
             return jsonify(summary) # Return if no recent metrics found

        where_clause = " OR ".join(["(hostname = ? AND timestamp_unix = ?)"] * len(hosts_to_query_final))
        cursor.execute(f'''
            SELECT hostname, network_total_sent_mbps, network_total_recv_mbps, peer_traffic
            FROM metrics
            WHERE {where_clause}
        ''', query_params)

        latest_metrics_rows = cursor.fetchall()

        total_network = 0.0
        total_peer = 0.0

        for row in latest_metrics_rows:
            # Sum network throughput
            sent = row['network_total_sent_mbps'] or 0.0
            recv = row['network_total_recv_mbps'] or 0.0
            total_network += (sent + recv)

            # Parse and sum peer traffic
            peer_traffic_json = row['peer_traffic']
            if peer_traffic_json:
                try:
                    peer_traffic_data = json.loads(peer_traffic_json)
                    if isinstance(peer_traffic_data, dict):
                        for flow_key, flow_data in peer_traffic_data.items():
                             if isinstance(flow_data, dict) and 'Mbps' in flow_data:
                                  rate = flow_data.get('Mbps', 0.0)
                                  total_peer += (rate if isinstance(rate, (float, int)) else 0.0)
                except (json.JSONDecodeError, TypeError):
                    # Ignore errors parsing peer traffic for summary
                    pass

        summary["total_network_throughput_mbps"] = round(total_network, 2)
        summary["total_peer_traffic_mbps"] = round(total_peer, 3)


    except sqlite3.Error as e:
        print(f"DB Error in /api/summary: {e}")
        # Return potentially partial data or an error
        return jsonify({"error": "Failed to query summary statistics"}), 500
    except Exception as e:
         print(f"Unexpected Error in /api/summary: {e}")
         import traceback
         traceback.print_exc()
         return jsonify({"error": "Unexpected server error"}), 500

    return jsonify(summary)

@app.route('/api/host_history/<hostname>')
def get_host_history(hostname):
    HISTORY_POINTS_MODAL = 60

    db = get_db()
    if not db: return jsonify({"error": "Database connection failed"}), 500

    # Initialize structure correctly
    history_data = {
        "timestamps": [],
        "cpu_percent": [],
        "mem_percent": [],
        "network_interfaces": {}, # { iface: {"sent_Mbps": [], "recv_Mbps": []} }
        "latest_link_speeds": {}
    }
    # --- DEBUG: Track interfaces expected based on LATEST row first ---
    latest_interfaces_dict = {}
    latest_network_interfaces_json = None

    try:
        cursor = db.cursor()

        # --- DEBUG: Fetch the single LATEST row first to get expected interfaces ---
        cursor.execute('''
            SELECT network_interfaces
            FROM metrics
            WHERE hostname = ?
            ORDER BY timestamp_unix DESC
            LIMIT 1
        ''', (hostname,))
        latest_row = cursor.fetchone()
        if latest_row:
             latest_network_interfaces_json = latest_row['network_interfaces']
             if latest_network_interfaces_json:
                 try:
                      latest_interfaces_dict = json.loads(latest_network_interfaces_json)
                      if not isinstance(latest_interfaces_dict, dict):
                           latest_interfaces_dict = {} # Reset if not a dict
                      else:
                           # --- Initialize history structure based on LATEST interfaces ---
                           for iface_name in latest_interfaces_dict:
                                history_data["network_interfaces"][iface_name] = {"sent_Mbps": [], "recv_Mbps": []}
                                # Store latest link speed here too
                                if isinstance(latest_interfaces_dict[iface_name], dict):
                                     history_data["latest_link_speeds"][iface_name] = latest_interfaces_dict[iface_name].get('link_speed_mbps', 0)

                 except (json.JSONDecodeError, TypeError) as e:
                      print(f"Warning: Could not parse LATEST network_interfaces JSON for {hostname}: {e}")
                      latest_interfaces_dict = {} # Reset on error
        # --- END DEBUG Fetch LATEST ---


        # Fetch the last N metric points for the host
        cursor.execute('''
            SELECT timestamp_utc, cpu_percent, mem_percent, network_interfaces
            FROM metrics
            WHERE hostname = ?
            ORDER BY timestamp_unix DESC
            LIMIT ?
        ''', (hostname, HISTORY_POINTS_MODAL))
        rows = cursor.fetchall()

        if not rows:
             cursor.execute("SELECT 1 FROM agents WHERE hostname = ?", (hostname,))
             if not cursor.fetchone(): return jsonify({"error": f"Host '{hostname}' not found"}), 404
             else: return jsonify(history_data) # Return empty structure if no metrics

        # Rows are newest first, reverse them for charting
        rows.reverse()

        # --- Process Rows (Oldest to Newest) ---
        expected_interfaces = set(history_data["network_interfaces"].keys()) # Interfaces found in latest row

        for row_index, row in enumerate(rows):
            history_data["timestamps"].append(row['timestamp_utc'])
            history_data["cpu_percent"].append(row['cpu_percent'])
            history_data["mem_percent"].append(row['mem_percent'])

            interfaces_in_this_row_json = row['network_interfaces']
            interfaces_seen_this_row = set()

            current_row_interfaces_data = {}
            if interfaces_in_this_row_json:
                try:
                    current_row_interfaces_data = json.loads(interfaces_in_this_row_json)
                    if not isinstance(current_row_interfaces_data, dict):
                        current_row_interfaces_data = {} # Ignore if not a dict
                except (json.JSONDecodeError, TypeError) as e:
                    print(f"Warning: Row {row_index} - Could not parse network_interfaces JSON for {hostname} at {row['timestamp_utc']}: {e}")
                    current_row_interfaces_data = {} # Treat as empty on error

            # Iterate through interfaces expected from the LATEST row
            for iface_name in expected_interfaces:
                iface_data_in_row = current_row_interfaces_data.get(iface_name)

                sent = None
                recv = None

                if isinstance(iface_data_in_row, dict):
                    sent = iface_data_in_row.get('sent_Mbps', None)
                    recv = iface_data_in_row.get('recv_Mbps', None)
                    interfaces_seen_this_row.add(iface_name) # Mark as seen

                # Append data (or None) for this interface for this timestamp
                # Ensure the list was initialized earlier
                if iface_name in history_data["network_interfaces"]:
                    history_data["network_interfaces"][iface_name]["sent_Mbps"].append(sent)
                    history_data["network_interfaces"][iface_name]["recv_Mbps"].append(recv)
                # else: # This case shouldn't happen if initialized from latest row
                #    print(f"Warning: Interface '{iface_name}' not pre-initialized.")


            # --- DEBUG: Check if lengths match after processing each row ---
            # current_ts_len = len(history_data["timestamps"])
            # for if_name, if_data in history_data["network_interfaces"].items():
            #     if len(if_data["sent_Mbps"]) != current_ts_len:
            #         print(f"!!! Length mismatch for {if_name} Sent after row {row_index}: {len(if_data['sent_Mbps'])} != {current_ts_len}")
            #     if len(if_data["recv_Mbps"]) != current_ts_len:
            #         print(f"!!! Length mismatch for {if_name} Recv after row {row_index}: {len(if_data['recv_Mbps'])} != {current_ts_len}")


        # Final length check (should be redundant now but safe)
        expected_length = len(history_data["timestamps"])
        for iface_name in list(history_data["network_interfaces"].keys()): # Iterate over copy of keys
             net_iface = history_data["network_interfaces"][iface_name]
             # Pad end if needed (unlikely with current logic but safe)
             while len(net_iface["sent_Mbps"]) < expected_length: net_iface["sent_Mbps"].append(None)
             while len(net_iface["recv_Mbps"]) < expected_length: net_iface["recv_Mbps"].append(None)
             # Trim if somehow too long (also unlikely)
             net_iface["sent_Mbps"] = net_iface["sent_Mbps"][:expected_length]
             net_iface["recv_Mbps"] = net_iface["recv_Mbps"][:expected_length]
             # --- DEBUG: Check if all values are null ---
             # if all(v is None for v in net_iface["sent_Mbps"]) and all(v is None for v in net_iface["recv_Mbps"]):
             #      print(f"Warning: Interface '{iface_name}' history contains only null values.")


    except sqlite3.Error as e:
        print(f"DB Error fetching history for {hostname}: {e}")
        return jsonify({"error": f"Database error fetching history for {hostname}"}), 500
    except Exception as e:
        print(f"Unexpected Error fetching history for {hostname}: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": "Unexpected server error fetching history"}), 500

    return jsonify(history_data)

@app.route('/api/connectivity_status')
def get_connectivity_status():
    global latest_agent_snapshot, snapshot_lock
    current_time = time.time()
    connectivity_data = {
        "nodes": [], # List of node info { id, name, is_collector }
        "links": []  # List of ping links { source, target, status, latency_ms }
    }
    active_nodes_snapshot = {}

    # --- 1. Get current snapshot and identify active nodes ---
    with snapshot_lock:
        snapshot_copy = copy.deepcopy(latest_agent_snapshot)

    active_host_info = {} # Store info for active agents { hostname: { ip, name } }
    db = get_db()
    if not db: return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = db.cursor()
        stale_limit_time = current_time - STALE_THRESHOLD_SECONDS
        cursor.execute('SELECT hostname, agent_ip FROM agents WHERE last_seen >= ?', (stale_limit_time,))
        active_rows = cursor.fetchall()
        for row in active_rows:
            active_host_info[row['hostname']] = { "ip": row['agent_ip'], "name": row['hostname'] }
    except sqlite3.Error as e:
         print(f"Warning: DB error fetching active agents for connectivity: {e}")
         # Continue with potentially incomplete data from snapshot only

    # --- 2. Build Node List (Collector + Active Agents) ---
    node_map = {} # Use map for quick lookup by ID (hostname or IP)

    # Add Collector Node
    collector_id = COLLECTOR_ID_FOR_GRAPH # Use ID defined at top
    node_map[collector_id] = {"id": collector_id, "name": COLLECTOR_HOSTNAME, "is_collector": True}

    # Add Active Agent Nodes from snapshot (use hostname as ID for consistency here)
    for hostname, agent_data in snapshot_copy.items():
        if hostname in active_host_info: # Check if considered active by DB query
             if hostname not in node_map: # Avoid overwriting collector if hostname is same
                  node_map[hostname] = {
                       "id": hostname, # Use hostname as ID for this API's node list
                       "name": hostname,
                       "is_collector": False,
                       "ip": active_host_info[hostname].get("ip") # Add IP if available
                  }

    connectivity_data["nodes"] = list(node_map.values())

    # --- 3. Build Link List from Ping Results ---
    processed_pairs = set()
    print(f"DEBUG Connectivity: Building links. Node map keys: {list(node_map.keys())}")
    print(f"DEBUG Connectivity: Active host info: {active_host_info}")

    for source_hostname, agent_data in snapshot_copy.items():
        if source_hostname not in active_host_info:
            # print(f"DEBUG Connectivity: Skipping inactive agent {source_hostname}")
            continue # Skip inactive agents

        source_node_id = source_hostname # Use hostname as source ID from node_map
        if source_node_id not in node_map:
            print(f"DEBUG Connectivity: Source node ID {source_node_id} not in node_map. Skipping pings.")
            continue # Should exist, but safety check

        ping_results = agent_data.get("latest_metrics", {}).get("ping_results", {})
        print(f"DEBUG Connectivity: Ping results for {source_hostname}: {ping_results}") # Log the results being processed

        if not isinstance(ping_results, dict):
             print(f"DEBUG Connectivity: Invalid ping_results format for {source_hostname}")
             continue

        for target_ip, result_data in ping_results.items():
            if not isinstance(result_data, dict):
                 print(f"DEBUG Connectivity: Invalid result_data for target {target_ip} from {source_hostname}")
                 continue

            target_node_id = None # Reset for each target IP

            # --- Revised Target Matching ---
            # 1. Check if target IP is the Collector's ID or Listen IP
            #    (Assuming COLLECTOR_ID_FOR_GRAPH is the pingable ID/IP for the collector)
            if target_ip == COLLECTOR_ID_FOR_GRAPH:
                 target_node_id = COLLECTOR_ID_FOR_GRAPH # Use the collector's consistent ID
                 print(f"DEBUG Connectivity: Matched {target_ip} to Collector Node ID {target_node_id}")
            # Check LISTEN_IP only if it's different and potentially pingable
            elif LISTEN_IP != "0.0.0.0" and target_ip == LISTEN_IP and COLLECTOR_ID_FOR_GRAPH in node_map:
                  target_node_id = COLLECTOR_ID_FOR_GRAPH
                  print(f"DEBUG Connectivity: Matched {target_ip} (ListenIP) to Collector Node ID {target_node_id}")
            else:
                # 2. Check if target IP belongs to an active agent
                found_agent_hostname = None
                for hn, info in active_host_info.items():
                     if info.get("ip") == target_ip:
                          found_agent_hostname = hn
                          break
                if found_agent_hostname and found_agent_hostname in node_map:
                     target_node_id = found_agent_hostname # Use agent's hostname as ID
                     print(f"DEBUG Connectivity: Matched {target_ip} to Agent Node ID {target_node_id}")

            # --- End Revised Target Matching ---


            if not target_node_id:
                 # print(f"DEBUG Connectivity: Ping target IP {target_ip} from {source_hostname} not found among active nodes/collector.")
                 continue # Skip pings to unknown/inactive targets

            # Ensure source and target are different nodes
            if source_node_id == target_node_id:
                 continue

            # Avoid duplicate links (A->B and B->A)
            pair_key = tuple(sorted((source_node_id, target_node_id)))
            if pair_key in processed_pairs:
                 continue
            processed_pairs.add(pair_key)

            print(f"DEBUG Connectivity: Adding link {source_node_id} -> {target_node_id}") # Log link creation
            connectivity_data["links"].append({
                "source": source_node_id,
                "target": target_node_id,
                "status": result_data.get("status", "unknown"),
                "latency_ms": result_data.get("latency_ms", None),
            })

    print(f"DEBUG Connectivity: Finished building links. Count: {len(connectivity_data['links'])}")
    return jsonify(connectivity_data)
def parse_metric_path(path_string):
    """
    Parses a metric path like 'network_interfaces.Ethernet.sent_Mbps'.
    Returns (column_name, json_path_or_none).
    Handles simple column names directly.
    Uses simple $.key1.key2 format for JSON path, assuming simple keys.
    """
    parts = path_string.strip().split('.')
    column_name = parts[0]
    if len(parts) == 1:
        return column_name, None # Simple column
    else:
        # --- Use simple dot notation without quotes ---
        json_path = '$'
        for part in parts[1:]:
             # Append directly using dot notation
             json_path += f'.{part}'
        # --- End simplification ---
        return column_name, json_path


# --- NEW: /api/history/range endpoint ---
@app.route('/api/history/range')
def get_history_range():
    # --- 1. Parse Query Parameters --- (unchanged)
    hostnames_str = request.args.get('hostnames', '')
    metrics_str = request.args.get('metrics', '')
    start_time_str = request.args.get('start_time')
    end_time_str = request.args.get('end_time')

    if not hostnames_str or not metrics_str or not start_time_str or not end_time_str:
        return jsonify({"error": "Missing required parameters: hostnames, metrics, start_time, end_time"}), 400

    hostnames = [h.strip() for h in hostnames_str.split(',') if h.strip()]
    metric_paths = [m.strip() for m in metrics_str.split(',') if m.strip()]

    if not hostnames or not metric_paths:
        return jsonify({"error": "Hostnames and metrics parameters cannot be empty"}), 400

    try:
        start_time_dt = dateutil_parser.isoparse(start_time_str)
        end_time_dt = dateutil_parser.isoparse(end_time_str)
        start_time_unix = start_time_dt.timestamp()
        end_time_unix = end_time_dt.timestamp()
    except ValueError as e:
        return jsonify({"error": f"Invalid timestamp format: {e}"}), 400

    # --- 2. Prepare DB Query ---
    db = get_db()
    if not db: return jsonify({"error": "Database connection failed"}), 500

    # --- WORKAROUND: Only select base columns, handle JSON extraction in Python ---
    select_columns = set(['hostname', 'timestamp_utc'])
    json_columns_needed = set()  # Track which JSON columns we need to fetch
    metric_path_map = {}  # Map original paths to (column, json_path) tuples for post-processing
    
    allowed_base_columns = {
        'timestamp_utc', 'timestamp_unix', 'interval_sec', 'cpu_percent', 'mem_percent',
        'disk_usage', 'disk_io', 'network_total_sent_mbps', 'network_total_recv_mbps',
        'network_interfaces', 'peer_traffic'
    }
    json_columns = {'disk_usage', 'disk_io', 'network_interfaces', 'peer_traffic'}

    # Parse metrics and determine which columns we need to fetch
    for path in metric_paths:
        try:
            column, json_path = parse_metric_path(path)
            if column not in allowed_base_columns:
                continue
                
            if json_path:
                if column not in json_columns:
                    continue
                json_columns_needed.add(column)  # We'll need to fetch this JSON column
            else:
                select_columns.add(column)  # Regular column, just add to select
                
            # Store mapping for post-processing
            metric_path_map[path] = (column, json_path)
            
        except Exception as e:
            print(f"Error parsing metric path '{path}': {e}")
            continue
            
    # Add any JSON columns we need to the select list
    select_columns.update(json_columns_needed)
    
    if not metric_path_map:
        return jsonify({"error": "No valid metrics specified after parsing."}), 400

    hostname_placeholders = ','.join('?' * len(hostnames))
    sql = f"""
        SELECT DISTINCT {', '.join(select_columns)}
        FROM metrics
        WHERE hostname IN ({hostname_placeholders})
          AND timestamp_unix >= ?
          AND timestamp_unix <= ?
        ORDER BY timestamp_unix ASC
    """
    params = hostnames + [start_time_unix, end_time_unix]

    # --- 3. Execute Query & Process Results ---
    results_by_host = {hostname: {metric: [] for metric in metric_paths} for hostname in hostnames}
    all_timestamps = []
    rows_processed = 0

    try:
        cursor = db.cursor()
        cursor.execute(sql, params)
        col_names = [desc[0] for desc in cursor.description]
        all_rows = cursor.fetchall()

        # Process timestamps
        processed_timestamps = set()
        for row_data in all_rows:
            row_dict = dict(zip(col_names, row_data))
            timestamp = row_dict.get('timestamp_utc')
            if timestamp and timestamp not in processed_timestamps:
                all_timestamps.append(timestamp)
                processed_timestamps.add(timestamp)

        # Create timestamp index mapping
        timestamp_to_index = {ts: i for i, ts in enumerate(all_timestamps)}
        final_timestamp_count = len(all_timestamps)

        # Initialize result lists
        for hn in results_by_host:
            for mp in results_by_host[hn]:
                results_by_host[hn][mp] = [None] * final_timestamp_count

        # Helper function to extract JSON value
        def extract_json_value(json_string, path):
            if not json_string:
                return None
                
            try:
                # Parse the JSON string
                data = json.loads(json_string) if isinstance(json_string, str) else json_string
                
                # Handle empty path case
                if not path:
                    return data
                    
                # Split path components (e.g., "$.interfaces.eth0.rx_bytes" -> ["interfaces", "eth0", "rx_bytes"])
                # Remove leading $. if present
                if path.startswith('$.'):
                    path = path[2:]
                    
                components = path.split('.')
                
                # Navigate through the JSON structure
                current = data
                for component in components:
                    if component in current:
                        current = current[component]
                    else:
                        return None  # Path doesn't exist
                        
                return current
            except (json.JSONDecodeError, AttributeError, TypeError):
                return None

        # Process each row
        for row_data in all_rows:
            rows_processed += 1
            row_dict = dict(zip(col_names, row_data))
            hostname = row_dict.get('hostname')
            timestamp = row_dict.get('timestamp_utc')

            if not hostname or hostname not in results_by_host or timestamp not in timestamp_to_index:
                continue

            target_index = timestamp_to_index[timestamp]

            # Process each requested metric for this row
            for original_path, (column, json_path) in metric_path_map.items():
                if column not in row_dict:
                    continue
                    
                if json_path:
                    # Handle JSON extraction in Python
                    json_value = extract_json_value(row_dict[column], json_path)
                    value = json_value
                else:
                    # Simple column value
                    value = row_dict[column]
                
                # Store the value
                if original_path in results_by_host[hostname]:
                    if 0 <= target_index < final_timestamp_count:
                        results_by_host[hostname][original_path][target_index] = value

    except sqlite3.Error as e:
        print(f"DB Error executing history query: {e}")
        return jsonify({"error": "Database error executing history query"}), 500
    except Exception as e:
        print(f"Unexpected Error processing history results: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": "Unexpected server error processing history results"}), 500

    # --- 4. Format Final Response ---
    final_response = {
        "timestamps": all_timestamps,
        "results": results_by_host
    }

    print(f"History query processed {rows_processed} rows, returning {len(all_timestamps)} timestamps.")
    return jsonify(final_response)
@app.route('/')
def index():
    return render_template('index.html')

# --- Main Execution ---
if __name__ == '__main__':
    init_db() # <-- Initialize DB on startup

    # Start the background thread for agent down checks
    down_checker = threading.Thread(target=check_agent_down_status, daemon=True)
    down_checker.start()
    
     # Start the background thread for data cleanup <-- ADDED
    cleanup_scheduler = threading.Thread(target=run_cleanup_scheduler, daemon=True)
    cleanup_scheduler.start() # <-- ADDED

    print(f"Simple UI collector server starting at http://{LISTEN_IP}:{LISTEN_PORT}")
    # Use Flask's built-in server for dev/testing
    # Consider Waitress or Gunicorn for production
    app.run(host=LISTEN_IP, port=LISTEN_PORT, debug=False, threaded=True) # Use threaded for handling multiple requests + background task