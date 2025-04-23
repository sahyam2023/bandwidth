# simple_ui_collector.py
from flask import Flask, request, jsonify, render_template
import datetime
import time
import threading
import ipaddress
from collections import deque # <-- IMPORTED
import copy # <-- IMPORTED for deep copies
import sys
import socket

# --- Configuration ---
LISTEN_IP = "0.0.0.0"
LISTEN_PORT = 8000
STALE_THRESHOLD_SECONDS = 120
HISTORY_LENGTH = 60 # 
NETWORK_CHOKE_THRESHOLD_PERCENT = 80.0
GRAPH_LINK_MBPS_THRESHOLD = 0.05
try:
    # Getting FQDN might be better if available
    collector_hostname_display = socket.getfqdn()
    if collector_hostname_display == 'localhost': # getfqdn sometimes returns localhost
         collector_hostname_display = socket.gethostname()

    # Try getting IP associated with hostname, might not be the listen IP
    try:
        collector_ip_internal = socket.gethostbyname(collector_hostname_display)
    except socket.gaierror:
        collector_ip_internal = "IP Lookup Failed" # Or use LISTEN_IP?

    # Use a combination for display, but a stable ID
    COLLECTOR_NODE_ID = "collector_node_main" # Stable ID
    COLLECTOR_NODE_NAME = f"Collector: {collector_hostname_display}"
    # Could store COLLECTOR_IP_INTERNAL if needed elsewhere

except Exception as e: # Catch any exception during detection
    print(f"Warning: Could not auto-detect collector hostname/IP: {e}")
    COLLECTOR_NODE_ID = "collector_node_main"
    COLLECTOR_NODE_NAME = "Collector (Detection Failed)"

# --- End Configuration ---

app = Flask(__name__)

# --- MODIFIED In-memory storage ---
agent_data_store = {} # Holds latest data and history
# Structure:
# {
#   "hostname": {
#       "last_seen": timestamp,
#       "latest_metrics": { ... processed metrics from extract_key_metrics ... },
#       "history": {
#           "timestamps": deque(maxlen=HISTORY_LENGTH),
#           "cpu_percent": deque(maxlen=HISTORY_LENGTH),
#           "mem_percent": deque(maxlen=HISTORY_LENGTH),
#           "network_interfaces": {
#               "iface_name": {
#                   "sent_Mbps": deque(maxlen=HISTORY_LENGTH),
#                   "recv_Mbps": deque(maxlen=HISTORY_LENGTH),
#               },
#               # ... other interfaces
#           }
#       }
#   }
# }
data_lock = threading.Lock()

# --- Keep extract_key_metrics function (mostly as is) ---
# It's still useful for processing the *latest* data point
# def extract_key_metrics(payload):
#     # ... (keep existing implementation, it processes one payload) ...
#     # Make sure it handles missing keys gracefully returning defaults (e.g., -1.0 or {})
#     network_payload = payload.get('network', {})
#     metrics = {
#         'hostname': payload.get('hostname', 'Unknown'),
#         'agent_ip': payload.get('agent_ip', 'N/A'),
#         'cpu_percent': payload.get('cpu', {}).get('percent', -1.0),
#         'mem_percent': payload.get('memory', {}).get('percent', -1.0),
#         'total_throughput_mbps': network_payload.get('total', {}).get('throughput_Mbps', -1.0),
#         'sent_mbps': network_payload.get('total', {}).get('sent_Mbps', -1.0),
#         'recv_mbps': network_payload.get('total', {}).get('recv_Mbps', -1.0),
#         'total_nic_speed_mbps': network_payload.get('reported_total_link_speed_mbps', 0),
#         'disks': {},
#         'network_adapters': {}, # Processed NIC data (utilization, speed)
#         'peer_traffic': payload.get('peer_traffic', {}),
#         'timestamp_utc': payload.get('timestamp_utc', datetime.datetime.utcnow().isoformat() + "Z")
#     }
#     # --- Disk Processing --- (Keep as is)
#     disk_usage_payload = payload.get('disk_usage', {})
#     # ...
#     # --- Network Adapter Processing --- (Keep as is)
#     interfaces_payload = network_payload.get('interfaces', {}) # Use raw interfaces here
#     if interfaces_payload and isinstance(interfaces_payload, dict):
#          for adapter_name, adapter_data in interfaces_payload.items():
#              if isinstance(adapter_data, dict):
#                   # Calculate utilization based on *sent/recv* percentages from agent
#                   is_up = adapter_data.get('is_up', False)
#                   sent_percent = adapter_data.get('sent_percent_of_link', -1.0)
#                   recv_percent = adapter_data.get('recv_percent_of_link', -1.0)
#                   # Prioritize the higher of send/receive for overall utilization %
#                   valid_percents = [p for p in [sent_percent, recv_percent] if isinstance(p, (int, float)) and p >= 0]
#                   utilization = max(valid_percents) if valid_percents else -1.0
#                   is_choked = False
#                   if is_up and utilization >= NETWORK_CHOKE_THRESHOLD_PERCENT:
#                       is_choked = True
#                   metrics['network_adapters'][adapter_name] = {
#                       'is_up': adapter_data.get('is_up', False), # Get is_up status
#                       'utilization_percent': utilization,
#                       'link_speed_mbps': adapter_data.get('link_speed_mbps', 0),
#                       # Also add raw send/recv Mbps for clarity if needed
#                       'sent_Mbps': adapter_data.get('sent_Mbps', -1.0),
#                       'recv_Mbps': adapter_data.get('recv_Mbps', -1.0),
#                       'is_choked': is_choked
#                   }
#     return metrics
# --- Replace your existing extract_key_metrics function with this one ---
def extract_key_metrics(payload):
    """Extracts key metrics from the agent payload, including disk IO."""
    network_payload = payload.get('network', {})
    # Default disk usage to {} if not present
    disk_usage_payload = payload.get('disk_usage', {})
    # --- Get the new disk_io data (defaults to {} if missing) ---
    disk_io_payload = payload.get('disk_io', {}) # <-- ADDED LINE

    metrics = {
        'hostname': payload.get('hostname', 'Unknown'),
        'agent_ip': payload.get('agent_ip', 'N/A'),
        'cpu_percent': payload.get('cpu', {}).get('percent', -1.0),
        'mem_percent': payload.get('memory', {}).get('percent', -1.0),
        'total_throughput_mbps': network_payload.get('total', {}).get('throughput_Mbps', -1.0),
        'sent_mbps': network_payload.get('total', {}).get('sent_Mbps', -1.0),
        'recv_mbps': network_payload.get('total', {}).get('recv_Mbps', -1.0),
        'total_nic_speed_mbps': network_payload.get('reported_total_link_speed_mbps', 0),
        'disks': {}, # Processed disk usage
        'network_adapters': {}, # Processed NIC data
        'peer_traffic': payload.get('peer_traffic', {}),
        # --- Store the received disk_io data ---
        'disk_io': disk_io_payload, # <-- ADDED LINE
        # --- End modification ---
        'timestamp_utc': payload.get('timestamp_utc', datetime.datetime.utcnow().isoformat() + "Z")
    }

    # --- Disk Usage Processing (Keep this part as it was) ---
    if isinstance(disk_usage_payload, dict):
        for disk_key, disk_data in disk_usage_payload.items():
             if isinstance(disk_data, dict):
                  metrics['disks'][disk_key] = {
                       'percent': disk_data.get('percent', -1.0),
                       'free_gb': disk_data.get('free_gb', -1.0),
                       'total_gb': disk_data.get('total_gb', -1.0),
                  }

    # --- Network Adapter Processing (Keep this part as it was) ---
    interfaces_payload = network_payload.get('interfaces', {})
    if interfaces_payload and isinstance(interfaces_payload, dict):
         for adapter_name, adapter_data in interfaces_payload.items():
             if isinstance(adapter_data, dict):
                  is_up = adapter_data.get('is_up', False)
                  sent_percent = adapter_data.get('sent_percent_of_link', -1.0)
                  recv_percent = adapter_data.get('recv_percent_of_link', -1.0)
                  valid_percents = [p for p in [sent_percent, recv_percent] if isinstance(p, (int, float)) and p >= 0]
                  utilization = max(valid_percents) if valid_percents else -1.0
                  is_choked = is_up and utilization >= NETWORK_CHOKE_THRESHOLD_PERCENT
                  metrics['network_adapters'][adapter_name] = {
                      'is_up': is_up,
                      'utilization_percent': utilization,
                      'link_speed_mbps': adapter_data.get('link_speed_mbps', 0),
                      'sent_Mbps': adapter_data.get('sent_Mbps', -1.0),
                      'recv_Mbps': adapter_data.get('recv_Mbps', -1.0),
                      'is_choked': is_choked
                  }
    return metrics
# --- End of the modified extract_key_metrics function ---

# --- MODIFIED /data endpoint ---
@app.route('/data', methods=['POST'])
def receive_agent_data():
    if not request.is_json: return jsonify({"error": "Request must be JSON"}), 400
    payload = request.get_json() # The raw payload from the agent
    if not payload or not isinstance(payload, dict): return jsonify({"error": "No valid JSON data received"}), 400

    # --- Validation (keep hostname/IP validation as is) ---
    hostname = payload.get('hostname')
    agent_ip = payload.get('agent_ip')
    # ... (rest of hostname/IP validation logic) ...
    if not hostname or not isinstance(hostname, str) or not hostname.strip():
         hostname = f"ip_{agent_ip}" # Fallback hostname
         payload['hostname'] = hostname # Ensure payload has hostname
    if not agent_ip or not isinstance(agent_ip, str):
        agent_ip = request.remote_addr
        payload['agent_ip'] = agent_ip
    try:
        ipaddress.ip_address(agent_ip)
    except ValueError:
         print(f"ERROR: Received invalid agent IP format '{agent_ip}' from remote {request.remote_addr}. Rejecting.")
         return jsonify({"error": f"Invalid agent_ip format: {agent_ip}"}), 400
    # --- End Validation ---

    timestamp = time.time()
    utc_timestamp_str = payload.get('timestamp_utc', datetime.datetime.utcnow().isoformat() + "Z")

    try:
        # Process the *latest* metrics using the existing function
        latest_processed_metrics = extract_key_metrics(payload)

        with data_lock:
            # --- Initialize host data if it's the first time ---
            if hostname not in agent_data_store:
                agent_data_store[hostname] = {
                    "last_seen": timestamp,
                    "latest_metrics": latest_processed_metrics, # Store initial processed data
                    "history": {
                        "timestamps": deque(maxlen=HISTORY_LENGTH),
                        "cpu_percent": deque(maxlen=HISTORY_LENGTH),
                        "mem_percent": deque(maxlen=HISTORY_LENGTH),
                        "network_interfaces": {} # Initialize per-interface history dict
                    }
                }
                print(f"Initialized data store for new host: {hostname} ({agent_ip})")

            # --- Update latest seen time and metrics ---
            host_entry = agent_data_store[hostname]
            host_entry["last_seen"] = timestamp
            host_entry["latest_metrics"] = latest_processed_metrics

            # --- Append to history deques ---
            history = host_entry["history"]
            history["timestamps"].append(utc_timestamp_str) # Store ISO timestamp string

            # Append CPU and Memory % (handle potential errors/missing data)
            history["cpu_percent"].append(payload.get('cpu', {}).get('percent', None)) # Append None if missing
            history["mem_percent"].append(payload.get('memory', {}).get('percent', None)) # Append None if missing

            # Append Network Interface history
            raw_interfaces = payload.get('network', {}).get('interfaces', {})
            if isinstance(raw_interfaces, dict):
                active_ifaces_in_payload = set(raw_interfaces.keys())
                history_ifaces = history["network_interfaces"]

                # Append data for interfaces present in this payload
                for iface_name, iface_data in raw_interfaces.items():
                    if not isinstance(iface_data, dict): continue # Skip invalid data

                    # Initialize deque for this interface if first time seen
                    if iface_name not in history_ifaces:
                        history_ifaces[iface_name] = {
                            "sent_Mbps": deque(maxlen=HISTORY_LENGTH),
                            "recv_Mbps": deque(maxlen=HISTORY_LENGTH),
                        }
                        print(f"  Initializing history for interface '{iface_name}' on host '{hostname}'")

                    # Append send/recv, appending None if missing in payload
                    history_ifaces[iface_name]["sent_Mbps"].append(iface_data.get('sent_Mbps', None))
                    history_ifaces[iface_name]["recv_Mbps"].append(iface_data.get('recv_Mbps', None))

                # Optional: Handle interfaces that *were* in history but *not* in this payload
                # (e.g., append None to mark missing data for this interval)
                missing_ifaces = set(history_ifaces.keys()) - active_ifaces_in_payload
                for iface_name in missing_ifaces:
                     # Check if the interface still exists in the latest processed metrics (might have gone down)
                     if iface_name in latest_processed_metrics.get('network_adapters', {}):
                         history_ifaces[iface_name]["sent_Mbps"].append(None)
                         history_ifaces[iface_name]["recv_Mbps"].append(None)
                     else:
                          # Interface seems gone, maybe remove from history? Or keep padding None?
                          # For simplicity, let's keep padding for now.
                          history_ifaces[iface_name]["sent_Mbps"].append(None)
                          history_ifaces[iface_name]["recv_Mbps"].append(None)


        # print(f"Received update from {hostname} ({agent_ip})") # Keep this less verbose maybe
        sys.stdout.write('+'); sys.stdout.flush() # Use '+' for history updates
        return jsonify({"status": "success"}), 200

    except Exception as e:
        print(f"\nError processing data from {hostname}: {e}")
        import traceback
        traceback.print_exc() # Print full traceback for debugging
        return jsonify({"error": "Internal server error processing data"}), 500


# --- MODIFIED /api/latest_data endpoint ---
# Now fetches from agent_data_store and extracts 'latest_metrics'
@app.route('/api/latest_data')
def get_latest_data():
    current_time = time.time()
    active_data = {}
    with data_lock:
        # Create a shallow copy of the keys to iterate safely
        hostnames = list(agent_data_store.keys())

    # Process outside the lock
    for hostname in hostnames:
        agent_info = None
        with data_lock: # Lock needed briefly to access shared data
             if hostname in agent_data_store:
                  # Make a deep copy to avoid modifying the original store later
                  agent_info = copy.deepcopy(agent_data_store[hostname])

        if agent_info:
            last_seen = agent_info.get('last_seen', 0)
            if (current_time - last_seen) <= STALE_THRESHOLD_SECONDS:
                # Add relative time directly to the 'latest_metrics' part
                if last_seen:
                    seconds_past = current_time - last_seen
                    if 'latest_metrics' in agent_info:
                        agent_info['latest_metrics']['last_seen_relative'] = format_time_ago(seconds_past)
                    else:
                        agent_info['latest_metrics'] = {'last_seen_relative': 'Error: Missing data'}
                else:
                    if 'latest_metrics' in agent_info:
                        agent_info['latest_metrics']['last_seen_relative'] = 'N/A'
                    else:
                        agent_info['latest_metrics'] = {'last_seen_relative': 'N/A'}

                # Return the structure expected by the frontend dashboard: { hostname: { data: latest_metrics, last_seen: ... } }
                active_data[hostname] = {
                    "data": agent_info['latest_metrics'], # The processed latest data
                    "last_seen": last_seen # Keep original last_seen if needed elsewhere
                }

    return jsonify(active_data)


# --- NEW /api/host_history/<hostname> endpoint ---
@app.route('/api/host_history/<hostname>')
def get_host_history(hostname):
    with data_lock:
        if hostname not in agent_data_store:
            return jsonify({"error": "Host not found"}), 404

        host_entry = agent_data_store[hostname]
        history = host_entry.get("history")

        if not history:
             return jsonify({"error": "History data not available for this host"}), 404

        # Convert deques to lists for JSON serialization
        history_data = {
            "timestamps": list(history.get("timestamps", deque())),
            "cpu_percent": list(history.get("cpu_percent", deque())),
            "mem_percent": list(history.get("mem_percent", deque())),
            "network_interfaces": {
                iface: {
                    "sent_Mbps": list(iface_hist.get("sent_Mbps", deque())),
                    "recv_Mbps": list(iface_hist.get("recv_Mbps", deque()))
                }
                for iface, iface_hist in history.get("network_interfaces", {}).items()
            }
        }
        # Also include latest static info if needed by charts (e.g., NIC speed)
        history_data["latest_link_speeds"] = {
             name: data.get("link_speed_mbps", 0)
             for name, data in host_entry.get("latest_metrics", {}).get("network_adapters", {}).items()
        }

    return jsonify(history_data)

# --- Keep get_peer_ips, format_time_ago, index, main execution ---
# ... (make sure get_peer_ips uses the new agent_data_store structure) ...
@app.route('/api/get_peer_ips')
def get_peer_ips():
    active_ips = set()
    current_time = time.time()
    with data_lock:
        # Iterate safely
        hostnames = list(agent_data_store.keys())

    for hostname in hostnames:
         agent_info = None
         with data_lock: # Lock briefly
             if hostname in agent_data_store:
                 agent_info = agent_data_store[hostname] # No deep copy needed here

         if agent_info:
             last_seen = agent_info.get('last_seen', 0)
             if (current_time - last_seen) <= STALE_THRESHOLD_SECONDS:
                 # Get IP from the latest_metrics section
                 agent_ip = agent_info.get('latest_metrics', {}).get('agent_ip')
                 if agent_ip and agent_ip != 'N/A':
                      try:
                          ipaddress.ip_address(agent_ip)
                          active_ips.add(agent_ip)
                      except ValueError:
                          print(f"Warning: Invalid IP format '{agent_ip}' stored for active host '{hostname}', skipping.")

    # print(f"API: Sending active peer IP list: {list(active_ips)}") # Can be noisy
    return jsonify(list(active_ips))

# *** DELETE your old get_all_peer_flows function ***
# *** PASTE THIS new version in its place ***

@app.route('/api/all_peer_flows')
def get_all_peer_flows():
    """
    Aggregates data for a graph view showing Collector, Agents, and Peer Traffic.
    Includes hostnames and filters low-traffic links if desired.
    Ensures peer links are only between known, active agents.
    """
    nodes_dict = {} # Using dict for efficient node lookup/update
    links = []
    current_time = time.time()

    # --- Add Collector Node ---
    nodes_dict[COLLECTOR_NODE_ID] = {
        "id": COLLECTOR_NODE_ID,
        "name": COLLECTOR_NODE_NAME,
        "hostname": COLLECTOR_NODE_NAME, # Use detected name
        "is_collector": True,
    }

    with data_lock:
        # Create a snapshot to work with, preventing modification issues during iteration
        current_data_snapshot = copy.deepcopy(agent_data_store)

    ip_to_hostname = {} # Map active agent IPs to their hostnames
    active_agent_ips = set() # Set of IPs for active agents

    # --- Pass 1: Identify active agents and build IP -> Hostname map ---
    # This pass ensures we only consider agents that have reported recently
    # and builds necessary lookup structures.
    for hostname, agent_info in current_data_snapshot.items():
         last_seen = agent_info.get('last_seen', 0)
         # Check if the agent is considered active based on the threshold
         if (current_time - last_seen) <= STALE_THRESHOLD_SECONDS:
             latest_metrics = agent_info.get('latest_metrics', {})
             agent_ip = latest_metrics.get('agent_ip', None)
             # Validate the agent's IP format
             if agent_ip and agent_ip != 'N/A':
                 try:
                     ipaddress.ip_address(agent_ip) # Check if valid IP format
                     # Store the mapping from IP to the hostname reported by the agent
                     ip_to_hostname[agent_ip] = hostname
                     # Add the IP to the set of active agents
                     active_agent_ips.add(agent_ip)
                     # Add/Update the agent node in our nodes dictionary.
                     # Uses the agent-reported hostname for display.
                     nodes_dict[agent_ip] = {
                         "id": agent_ip, # Use IP as the stable ID for the node
                         "name": hostname, # Use agent-reported hostname as the display name
                         "hostname": hostname, # Store hostname field as well
                         "is_collector": False
                     }
                 except ValueError:
                     print(f"Warning: Invalid agent_ip format '{agent_ip}' for hostname '{hostname}' in Pass 1. Skipping agent.")
                     pass # Ignore agents reporting invalid IPs

    # --- Pass 2: Create Links (Collector->Agent and Peer<->Peer) ---
    # processed_peer_links = set() # Optional: Use to prevent duplicate directed links if needed

    # Iterate through the data associated with only the ACTIVE agents identified in Pass 1
    for agent_ip in active_agent_ips:
         # --- Add Collector -> Agent Link ---
         # This link represents the reporting relationship, not measured traffic.
         links.append({
             "source": COLLECTOR_NODE_ID,
             "target": agent_ip,
             "type": "reporting", # Indicates it's a structural/reporting link
             "rate_mbps": 0 # No traffic rate associated with this link type
         })

         # --- Process Peer Traffic reported BY this agent ---
         # Get the hostname corresponding to the current agent's IP
         agent_hostname = ip_to_hostname.get(agent_ip)
         if not agent_hostname: continue # Safety check, should exist if in active_agent_ips

         # Access the agent's data from the snapshot using its hostname
         agent_info = current_data_snapshot.get(agent_hostname, {})
         latest_metrics = agent_info.get('latest_metrics', {})
         peer_traffic_data = latest_metrics.get('peer_traffic', {}) # Get the peer traffic dict

         # Check if peer_traffic_data is a dictionary before iterating
         if isinstance(peer_traffic_data, dict):
             # Iterate through each flow reported by this agent
             for flow_key, flow_data in peer_traffic_data.items():
                 # Basic validation of the flow data structure
                 if not isinstance(flow_data, dict) or 'Mbps' not in flow_data:
                     print(f"Warning: Invalid flow_data format for key '{flow_key}' from agent '{agent_hostname}'. Skipping.")
                     continue

                 try:
                     # Attempt to parse the source and target IPs from the flow key (e.g., "1.2.3.4_to_5.6.7.8")
                     source_ip, target_ip = flow_key.split('_to_')
                     # Get the traffic rate, default to 0 if missing or invalid
                     rate_mbps = round(float(flow_data.get('Mbps', 0.0)), 3)

                     # --- *** CORE VALIDATION LOGIC *** ---
                     # 1. Validate BOTH source and target IPs are valid formats
                     ipaddress.ip_address(source_ip)
                     ipaddress.ip_address(target_ip)

                     # 2. CRITICAL CHECK: Ensure BOTH source and target IPs belong to ACTIVE agents
                     #    This prevents creating links to/from agents that haven't reported recently
                     #    or agents whose IPs weren't valid in Pass 1.
                     if source_ip not in active_agent_ips or target_ip not in active_agent_ips:
                         # Uncomment for debugging if links are missing:
                         # print(f"Debug: Skipping peer link {source_ip} -> {target_ip} (Rate: {rate_mbps} Mbps) because one/both IPs are not in the active agent set: {active_agent_ips}")
                         continue # Skip this link if either end isn't an active agent

                     # 3. Optional: Filter links based on minimum traffic threshold
                     if rate_mbps < GRAPH_LINK_MBPS_THRESHOLD:
                         # Uncomment for debugging:
                         # print(f"Debug: Skipping peer link {source_ip} -> {target_ip} due to low rate: {rate_mbps} Mbps (Threshold: {GRAPH_LINK_MBPS_THRESHOLD})")
                         continue # Skip links below the minimum threshold

                     # --- Optional: Avoid adding the exact same directed link twice ---
                     # If agent A reports A->B and agent B also reports A->B, this prevents duplicates.
                     # If A reports A->B and B reports B->A, this allows both distinct links.
                     # link_tuple = (source_ip, target_ip)
                     # if link_tuple in processed_peer_links:
                     #    continue
                     # processed_peer_links.add(link_tuple)
                     # ---

                     # If all checks passed, add the peer traffic link to our list
                     links.append({
                         "source": source_ip, # The source node ID (IP address)
                         "target": target_ip, # The target node ID (IP address)
                         "type": "peer_traffic", # Indicates the type of link
                         "rate_mbps": rate_mbps # The measured traffic rate
                     })

                 except ValueError:
                     # Handles errors from ipaddress.ip_address() or flow_key.split()
                     print(f"Warning: Could not parse peer flow key '{flow_key}' or IPs are invalid (reported by {agent_hostname}). Skipping.")
                     continue # Skip this specific flow key
                 except Exception as e:
                     # Catch any other unexpected errors during processing of this flow
                     print(f"Warning: Error processing peer flow '{flow_key}' from agent '{agent_hostname}': {e}. Skipping.")
                     continue # Skip this specific flow key

    # Convert the nodes dictionary values (which are the node objects) into a list
    node_list = list(nodes_dict.values())

    # Prepare the final graph data structure for JSON response
    graph_data = { "nodes": node_list, "links": links }
    # print("Debug: Returning /api/all_peer_flows data:", graph_data) # Uncomment for deep debugging
    return jsonify(graph_data)

# --- End of the revised get_all_peer_flows function ---
# --- Keep format_time_ago, index, main execution ---
def format_time_ago(seconds_past):
    """Helper to format relative time"""
    if not isinstance(seconds_past, (int, float)) or seconds_past < 0: return 'N/A'
    seconds_past = int(seconds_past) # Convert to int for display
    if seconds_past < 60: return f"{seconds_past}s ago"
    if seconds_past < 3600: return f"{seconds_past // 60}m ago"
    # Add hours if needed, e.g.
    # if seconds_past < 86400: return f"{seconds_past // 3600}h ago"
    return f"{seconds_past // 3600}h ago" # Default to hours for longer


@app.route('/')
def index():
    """Serve the main dashboard page using the template"""
    return render_template('index.html')

# --- Main Execution ---
if __name__ == '__main__':
    print(f"Simple UI collector server starting at http://{LISTEN_IP}:{LISTEN_PORT}")
    # Consider using a production WSGI server like Waitress or Gunicorn:
    # from waitress import serve
    # serve(app, host=LISTEN_IP, port=LISTEN_PORT)
    app.run(host=LISTEN_IP, port=LISTEN_PORT, debug=False, threaded=True)