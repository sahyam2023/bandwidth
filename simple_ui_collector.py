from flask import Flask, request, jsonify, render_template, g # Added g for context
import datetime
import time
import threading
import ipaddress
# Removed deque as history is in DB now
import copy
import sys
import sqlite3 # <-- IMPORTED
import json     # <-- IMPORTED

# --- Configuration ---
LISTEN_IP = "0.0.0.0"
LISTEN_PORT = 8000
STALE_THRESHOLD_SECONDS = 120
# HISTORY_LENGTH removed - history is now in DB
NETWORK_CHOKE_THRESHOLD_PERCENT = 80.0
DATABASE = 'collector_data.db' # <-- NEW: Database file path

# --- Alerting Thresholds (Example) ---
ALERT_CPU_THRESHOLD = 90.0
ALERT_MEM_THRESHOLD = 90.0
ALERT_DISK_THRESHOLD = 95.0 # Check against individual disk 'percent'
ALERT_AGENT_DOWN_SECONDS = STALE_THRESHOLD_SECONDS * 2 # When to consider agent down


# --- Data Cleanup Configuration ---
DATA_RETENTION_DAYS = 7  # <---- CHANGED FROM 60 to 7
CLEANUP_INTERVAL_HOURS = 24
CLEANUP_BATCH_SIZE = 5000

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
            # Create indexes for faster lookups
            print("Creating indexes (if not exists)...")
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_metrics_hostname_timestamp ON metrics (hostname, timestamp_unix DESC)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_agents_last_seen ON agents (last_seen DESC)')

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
        'timestamp_utc': payload.get('timestamp_utc', datetime.datetime.utcnow().isoformat() + "Z")
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
    """Checks latest metrics against thresholds and updates active_alerts."""
    global active_alerts, alerts_lock
    now = time.time()
    alerts_changed = False

    # --- Check CPU ---
    cpu_alert_id = f"{hostname}_cpu_high"
    cpu_percent = latest_metrics.get('cpu_percent', -1.0)
    if isinstance(cpu_percent, (int, float)) and cpu_percent >= ALERT_CPU_THRESHOLD:
        if cpu_alert_id not in active_alerts:
            print(f"ALERT TRIGGERED: High CPU for {hostname} ({cpu_percent}%)")
            with alerts_lock:
                active_alerts[cpu_alert_id] = {
                    "hostname": hostname, "type": "cpu_high", "status": "active",
                    "message": f"CPU usage {cpu_percent}% >= {ALERT_CPU_THRESHOLD}%",
                    "value": cpu_percent, "threshold": ALERT_CPU_THRESHOLD, "start_time": now, "last_active_time": now
                }
            alerts_changed = True
        else:
             # Update last active time if already firing
             with alerts_lock:
                  if cpu_alert_id in active_alerts: # Check again inside lock
                      active_alerts[cpu_alert_id]['last_active_time'] = now
                      active_alerts[cpu_alert_id]['value'] = cpu_percent # Update value

    elif cpu_alert_id in active_alerts: # CPU is OK now, resolve alert
        print(f"ALERT RESOLVED: High CPU for {hostname}")
        with alerts_lock:
            if active_alerts.pop(cpu_alert_id, None): # Remove if exists
                 alerts_changed = True

    # --- Check Memory ---
    mem_alert_id = f"{hostname}_mem_high"
    mem_percent = latest_metrics.get('mem_percent', -1.0)
    if isinstance(mem_percent, (int, float)) and mem_percent >= ALERT_MEM_THRESHOLD:
         if mem_alert_id not in active_alerts:
             print(f"ALERT TRIGGERED: High Memory for {hostname} ({mem_percent}%)")
             with alerts_lock:
                 active_alerts[mem_alert_id] = {
                     "hostname": hostname, "type": "mem_high", "status": "active",
                     "message": f"Memory usage {mem_percent}% >= {ALERT_MEM_THRESHOLD}%",
                     "value": mem_percent, "threshold": ALERT_MEM_THRESHOLD, "start_time": now, "last_active_time": now
                 }
             alerts_changed = True
         else:
              with alerts_lock:
                   if mem_alert_id in active_alerts:
                       active_alerts[mem_alert_id]['last_active_time'] = now
                       active_alerts[mem_alert_id]['value'] = mem_percent
    elif mem_alert_id in active_alerts:
        print(f"ALERT RESOLVED: High Memory for {hostname}")
        with alerts_lock:
             if active_alerts.pop(mem_alert_id, None):
                  alerts_changed = True

    # --- Check Disk Usage ---
    disks = latest_metrics.get('disks', {})
    if isinstance(disks, dict):
        for disk_name, disk_data in disks.items():
            disk_alert_id = f"{hostname}_disk_high_{disk_name}"
            disk_percent = disk_data.get('percent', -1.0)
            if isinstance(disk_percent, (int, float)) and disk_percent >= ALERT_DISK_THRESHOLD:
                if disk_alert_id not in active_alerts:
                    print(f"ALERT TRIGGERED: High Disk Usage for {hostname} - {disk_name} ({disk_percent}%)")
                    with alerts_lock:
                        active_alerts[disk_alert_id] = {
                            "hostname": hostname, "type": "disk_high", "status": "active",
                            "message": f"Disk '{disk_name}' usage {disk_percent}% >= {ALERT_DISK_THRESHOLD}%",
                            "value": disk_percent, "threshold": ALERT_DISK_THRESHOLD, "start_time": now, "last_active_time": now
                        }
                    alerts_changed = True
                else:
                     with alerts_lock:
                          if disk_alert_id in active_alerts:
                              active_alerts[disk_alert_id]['last_active_time'] = now
                              active_alerts[disk_alert_id]['value'] = disk_percent
            elif disk_alert_id in active_alerts: # Disk OK now
                print(f"ALERT RESOLVED: High Disk Usage for {hostname} - {disk_name}")
                with alerts_lock:
                     if active_alerts.pop(disk_alert_id, None):
                          alerts_changed = True

    # --- Check Network Choke ---
    adapters = latest_metrics.get('network_adapters', {})
    if isinstance(adapters, dict):
        for adapter_name, adapter_data in adapters.items():
             choke_alert_id = f"{hostname}_net_choked_{adapter_name}"
             is_choked = adapter_data.get('is_choked', False)
             util_percent = adapter_data.get('utilization_percent', -1.0)
             if is_choked:
                  if choke_alert_id not in active_alerts:
                       print(f"ALERT TRIGGERED: Network Choked for {hostname} - {adapter_name} ({util_percent}%)")
                       with alerts_lock:
                           active_alerts[choke_alert_id] = {
                               "hostname": hostname, "type": "net_choked", "status": "active",
                               "message": f"Adapter '{adapter_name}' choked (Util: {util_percent}% >= {NETWORK_CHOKE_THRESHOLD_PERCENT}%)",
                               "value": util_percent, "threshold": NETWORK_CHOKE_THRESHOLD_PERCENT, "start_time": now, "last_active_time": now
                           }
                       alerts_changed = True
                  else:
                       with alerts_lock:
                            if choke_alert_id in active_alerts:
                                active_alerts[choke_alert_id]['last_active_time'] = now
                                active_alerts[choke_alert_id]['value'] = util_percent
             elif choke_alert_id in active_alerts: # Choke resolved
                  print(f"ALERT RESOLVED: Network Choked for {hostname} - {adapter_name}")
                  with alerts_lock:
                       if active_alerts.pop(choke_alert_id, None):
                            alerts_changed = True

    # --- Check Agent Down (Handled separately by background task) ---
    # We need to resolve agent_down alerts here if the agent just reported back
    agent_down_alert_id = f"{hostname}_agent_down"
    if agent_down_alert_id in active_alerts:
        print(f"ALERT RESOLVED: Agent Down for {hostname} (Agent reported back)")
        with alerts_lock:
             if active_alerts.pop(agent_down_alert_id, None):
                  alerts_changed = True

    # Return True if any alert state changed
    return alerts_changed


def check_agent_down_status():
    """Background task to check for agents that haven't reported recently."""
    global latest_agent_snapshot, active_alerts, snapshot_lock, alerts_lock
    print("Starting background agent down check...")
    while True:
        time.sleep(STALE_THRESHOLD_SECONDS / 2) # Check periodically
        now = time.time()
        alerts_changed = False
        hostnames_to_check = []

        # Get a snapshot of current hosts under lock
        with snapshot_lock:
            hostnames_to_check = list(latest_agent_snapshot.keys())

        # Check last seen time (no lock needed for read here after getting keys)
        for hostname in hostnames_to_check:
            last_seen = 0
            with snapshot_lock: # Need lock again briefly to get last_seen
                 if hostname in latest_agent_snapshot:
                      last_seen = latest_agent_snapshot[hostname].get('last_seen', 0)

            agent_down_alert_id = f"{hostname}_agent_down"
            is_down = (now - last_seen) > ALERT_AGENT_DOWN_SECONDS if last_seen > 0 else False # Agent is down if stale

            if is_down:
                if agent_down_alert_id not in active_alerts:
                     print(f"ALERT TRIGGERED: Agent Down for {hostname} (Last seen {format_time_ago(now - last_seen)})")
                     with alerts_lock:
                         active_alerts[agent_down_alert_id] = {
                             "hostname": hostname, "type": "agent_down", "status": "active",
                             "message": f"Agent has not reported in > {ALERT_AGENT_DOWN_SECONDS} seconds.",
                             "value": round(now - last_seen), "threshold": ALERT_AGENT_DOWN_SECONDS,
                             "start_time": now, "last_active_time": now # Start time is now, last active also now
                         }
                     alerts_changed = True
                else:
                     # Update last_active_time if already down
                     with alerts_lock:
                         if agent_down_alert_id in active_alerts:
                              active_alerts[agent_down_alert_id]['last_active_time'] = now
                              active_alerts[agent_down_alert_id]['value'] = round(now - last_seen)

            # Note: Agent down alerts are resolved in check_and_update_alerts when agent reports back

        # Optional: Clean up very old resolved alerts from memory if needed


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
    global latest_agent_snapshot, snapshot_lock, active_alerts, alerts_lock
    current_time = time.time()
    active_data = {}
    # Get snapshot under lock
    with snapshot_lock:
        # Create a copy to iterate over outside the lock
        snapshot_copy = copy.deepcopy(latest_agent_snapshot)
    
    # --- Get current alerting hostnames under lock ---
    alerting_hostnames = set()
    with alerts_lock:
        for alert_data in active_alerts.values():
            if alert_data.get("status") == "active":
                 alerting_hostnames.add(alert_data.get("hostname"))
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
    nodes = set()
    links = []
    current_time = time.time()
    stale_limit_time = current_time - STALE_THRESHOLD_SECONDS

    db = get_db()
    if not db: return jsonify({"error": "Database connection failed"}), 500

    active_agents = {} # Store latest metric info {hostname: {ip: ..., peer_traffic: ...}}

    try:
        cursor = db.cursor()
        # Get active hostnames and their IPs first
        cursor.execute('SELECT hostname, agent_ip FROM agents WHERE last_seen >= ?', (stale_limit_time,))
        active_host_rows = cursor.fetchall()

        if not active_host_rows:
            return jsonify({"nodes": [], "links": []}) # No active agents

        host_list = [row['hostname'] for row in active_host_rows]
        ip_map = {row['hostname']: row['agent_ip'] for row in active_host_rows}

        # Efficiently get the latest metric row ID for each active agent
        placeholders = ','.join('?' * len(host_list))
        cursor.execute(f'''
            SELECT hostname, MAX(timestamp_unix) as max_ts
            FROM metrics
            WHERE hostname IN ({placeholders})
            GROUP BY hostname
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
             return jsonify({"nodes": [], "links": []})

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
            hostname = row['hostname']
            agent_ip = ip_map.get(hostname)
            peer_traffic_json = row['peer_traffic']

            if not agent_ip or not peer_traffic_json: continue

            # Add agent IP to nodes
            if agent_ip != 'N/A':
                try:
                    ipaddress.ip_address(agent_ip)
                    nodes.add(agent_ip)
                except ValueError: pass # Ignore invalid agent IPs

            # Parse and process flows
            try:
                peer_traffic_data = json.loads(peer_traffic_json)
                if isinstance(peer_traffic_data, dict):
                    for flow_key, flow_data in peer_traffic_data.items():
                        if isinstance(flow_data, dict) and 'Mbps' in flow_data:
                            try:
                                source_ip, target_ip = flow_key.split('_to_')
                                rate_mbps = flow_data.get('Mbps', 0.0)
                                rate_mbps = round(rate_mbps, 3) if isinstance(rate_mbps, (float, int)) else 0.0

                                # Basic IP validation
                                ipaddress.ip_address(source_ip)
                                ipaddress.ip_address(target_ip)

                                nodes.add(source_ip)
                                nodes.add(target_ip)

                                links.append({
                                    "source": source_ip,
                                    "target": target_ip,
                                    "rate_mbps": rate_mbps
                                })
                            except (ValueError, KeyError, Exception):
                                continue # Skip invalid flows/IPs
            except (json.JSONDecodeError, TypeError) as e:
                 print(f"Warning: Could not parse peer_traffic JSON for {hostname}: {e}")
                 continue

    except sqlite3.Error as e:
        print(f"DB Error fetching peer flows: {e}")
        return jsonify({"error": "Failed to query peer flows"}), 500

    # Prepare final graph data structure
    node_list = [{"id": ip, "name": ip} for ip in nodes]
    graph_data = {"nodes": node_list, "links": links}
    return jsonify(graph_data)


# --- NEW /api/alerts endpoint ---
@app.route('/api/alerts')
def get_active_alerts():
    """Returns the current list of active alerts."""
    global active_alerts, alerts_lock
    # Return a copy of the dictionary under lock
    with alerts_lock:
        # Optionally filter or format data here if needed
        current_alerts_list = list(active_alerts.values()) # Convert dict to list

    # You could sort alerts here, e.g., by start_time
    # current_alerts_list.sort(key=lambda x: x.get('start_time', 0), reverse=True)

    return jsonify(current_alerts_list)

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