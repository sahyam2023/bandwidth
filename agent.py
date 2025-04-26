# agent.py
import psutil
import requests
import time
import socket
import sys
import json
import ipaddress
from collections import defaultdict, deque # Added deque
import datetime
import threading
import signal
import platform  # <-- NEW: For OS-specific ping command
import subprocess # <-- NEW: To run ping
import re 

# --- Packet Capture Dependencies ---
try:
    from scapy.all import sniff, IP, conf as scapy_conf
    NPCAP_AVAILABLE = True
    print("Scapy/Npcap found.")
except ImportError:
    print("WARNING: Scapy not found. Peer traffic sniffing disabled.")
    print("         Install Scapy (and Npcap/WinPcap on Windows) for peer traffic.")
    NPCAP_AVAILABLE = False
except OSError as e:
    print(f"WARNING: OSError importing Scapy (Npcap might be missing or not configured): {e}")
    print("         Peer traffic sniffing disabled. Ensure Npcap is installed and PATH is correct (Windows).")
    NPCAP_AVAILABLE = False
except Exception as e:
    print(f"WARNING: Unexpected error importing Scapy: {e}")
    NPCAP_AVAILABLE = False


# --- Configuration ---
COLLECTOR_IP = None # Will be asked from user
COLLECTOR_PORT = 8000
REPORT_INTERVAL_SECONDS = 2 # Frequency of reporting data to collector
PEER_IP_REFRESH_INTERVAL_SECONDS = 300 # How often to ask collector for peer list
SNIFF_INTERFACE = None # Set to specific interface name (e.g., "Ethernet", "eth0") to override auto-detect
DISKS_TO_MONITOR_USAGE = None # List of mount points (e.g. ["/", "/mnt/data"], or None for all physical)
INTERFACES_TO_MONITOR = None # List of NIC names (e.g. ["Ethernet", "eth0"] or None for all non-virtual)
PING_INTERVAL_SECONDS = 60 # How often to ping peers/collector
PING_TIMEOUT_SECONDS = 1   # Timeout for each individual ping command
PING_COUNT = 2
latest_ping_results = {} # { target_ip: {"status": "success/timeout/error", "latency_ms": float or None, "timestamp": float} }
ping_results_lock = threading.Lock()


# --- Global Variables ---
current_peer_ips = set()
peer_ip_lock = threading.Lock()
collector_peer_url = ""
peer_byte_counts = defaultdict(int)
peer_data_lock = threading.Lock()
stop_event = threading.Event()


# --- Helper Functions ---
def get_local_ip():
    """Tries various methods to get the local IP address."""
    s = None
    try:
        # Try connecting to a known external server (doesn't send data)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.1) # Prevent long wait if no network
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        return ip
    except socket.error:
        # Fallback: Get IP associated with hostname
        try:
            hostname = socket.gethostname()
            ip = socket.gethostbyname(hostname)
            # Avoid loopback unless it's the only option
            if ip.startswith("127.") and hostname != 'localhost':
                 # Try getting all interfaces
                 addr_info = socket.getaddrinfo(hostname, None)
                 for item in addr_info:
                      if item[0] == socket.AF_INET and not item[4][0].startswith("127."):
                           return item[4][0]
            return ip # Return loopback or primary hostname IP if needed
        except socket.gaierror:
            print("ERROR: Failed to get local IP using hostname.")
            return None
    finally:
        if s:
            s.close()

def get_collector_ip_from_user(detected_agent_ip):
    """Prompts the user to enter the collector's IP address."""
    print("-" * 30)
    print(f"Detected Agent IP: {detected_agent_ip or 'Could not detect automatically'}")
    print("Enter the IP address of the Collector server.")
    print("-" * 30)
    while True:
        collector_ip_str = input(f"Enter Collector IP: ").strip()
        if not collector_ip_str:
            continue
        try:
            ipaddress.ip_address(collector_ip_str)
            print(f"Collector IP set to: {collector_ip_str}")
            return collector_ip_str
        except ValueError:
            print(f"'{collector_ip_str}' is not a valid IP address. Please try again.")

def get_cpu_stats():
    """Gets the current overall CPU utilization percentage."""
    try:
        # interval=None gets usage since last call, 0.1 gets avg over 0.1s
        cpu_usage = psutil.cpu_percent(interval=0.1)
        return {"percent": cpu_usage}
    except Exception as e:
        print(f"Warning: Could not get CPU stats: {e}")
        return {"percent": -1.0} # Indicate error

def get_memory_stats():
    """Gets the current memory utilization percentage."""
    try:
        mem = psutil.virtual_memory()
        return {"percent": mem.percent}
    except Exception as e:
        print(f"Warning: Could not get Memory stats: {e}")
        return {"percent": -1.0} # Indicate error

def get_disk_usage_stats(mount_points_to_monitor=None):
    """
    Gets disk usage stats (percent, free GB, total GB) for specified or all physical mount points.
    """
    disk_stats = {}
    try:
        all_partitions = psutil.disk_partitions(all=False) # Only physical devices usually
    except Exception as e:
        print(f"Warning: Failed to list disk partitions: {e}")
        return disk_stats

    target_mount_points = set()
    if mount_points_to_monitor:
        # Normalize paths for comparison (remove trailing slash)
        target_mount_points = {mp.rstrip('\\/') for mp in mount_points_to_monitor}

    for part in all_partitions:
        # Basic filtering for physical-like devices
        is_physical_candidate = True
        if 'cdrom' in part.opts or part.fstype == '' or 'removable' in part.opts:
            is_physical_candidate = False # Skip CD-ROMs, empty fstype, removable media

        normalized_mountpoint = part.mountpoint.rstrip('\\/')

        monitor_this = False
        if mount_points_to_monitor is None: # If no specific list, monitor physical candidates
            monitor_this = is_physical_candidate
        elif normalized_mountpoint in target_mount_points: # If list provided, monitor if in list
            monitor_this = True

        if monitor_this:
            try:
                usage = psutil.disk_usage(part.mountpoint)
                # Create a simpler key (e.g., C or root instead of C: or /)
                key = normalized_mountpoint
                if ':' in key: # Windows drive letter
                     key = key.split(':')[0]
                elif key == '/':
                     key = 'root' # Common name for root filesystem
                else: # Use last part of path for other mounts
                     key = key.split('/')[-1] or key.split('\\')[-1] or 'unknown_mount'

                disk_stats[key] = {
                    "percent": usage.percent,
                    "free_gb": round(usage.free / (1024**3), 2),
                    "total_gb": round(usage.total / (1024**3), 2)
                }
            except PermissionError:
                print(f"Warning: Permission denied getting usage for {part.mountpoint}", file=sys.stderr)
            except Exception as e:
                print(f"Warning: Error getting usage for {part.mountpoint}: {e}", file=sys.stderr)

    return disk_stats


# --- Peer IP / Sniffing Functions ---
def get_peer_ips_from_collector(url):
    """ Fetches the list of active peer IPs from the collector """
    global current_peer_ips, peer_ip_lock
    print(f"Attempting to fetch peer IPs from {url}...")
    try:
        response = requests.get(url, timeout=(5, 15)) # (connect, read)
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
        ips_list = response.json()
        if not isinstance(ips_list, list):
            print(f"  ERROR: Collector returned non-list data for peer IPs: {type(ips_list)}")
            return False # Don't update if data is invalid
        valid_ips = set()
        for ip in ips_list:
            try:
                ipaddress.ip_address(ip)
                valid_ips.add(ip)
            except ValueError:
                print(f"  Warning: Received invalid IP format '{ip}' from collector, ignoring.")
        # --- Update the global set under lock ---
        with peer_ip_lock:
            old_count = len(current_peer_ips)
            current_peer_ips = valid_ips
            new_count = len(current_peer_ips)
        # --- End lock ---
        if new_count != old_count:
             print(f"  Successfully updated peer IPs. Previous: {old_count}, New: {new_count}. Peers: {current_peer_ips}")
        else:
             print(f"  Successfully refreshed peer IPs. Count remains {new_count}. Peers: {current_peer_ips}")
        return True
    except requests.exceptions.Timeout:
        print("  ERROR: Request timed out fetching peer IPs.")
        return False
    except requests.exceptions.ConnectionError:
        print(f"  ERROR: Could not connect to collector at {url} to fetch peer IPs.")
        return False
    except requests.exceptions.HTTPError as e:
         print(f"  ERROR: HTTP Error {e.response.status_code} fetching peer IPs.")
         try: print(f"  Collector Response: {e.response.text}") # Try to show response body
         except Exception: pass
         return False
    except requests.exceptions.RequestException as e:
        print(f"  ERROR: Failed to fetch peer IPs: {e}")
        return False
    except json.JSONDecodeError:
        print("  ERROR: Could not decode JSON response from collector for peer IPs.")
        return False
    except Exception as e:
        print(f"  ERROR: Unexpected error fetching peer IPs: {e}")
        return False

def refresh_peer_ips_periodically(url, interval, stop_evt):
    """ Periodically calls get_peer_ips_from_collector in a background thread """
    print(f"Peer IP refresh thread started (interval: {interval}s)")
    while not stop_evt.wait(interval): # Wait for interval OR stop signal
        if stop_evt.is_set(): # Check if stopped during wait
            break
        get_peer_ips_from_collector(url)
    print("Peer IP refresh thread stopped.")

def packet_handler(packet):
    """Callback function for scapy's sniff(). Processes each packet to count peer traffic."""
    global peer_byte_counts, peer_data_lock, current_peer_ips, peer_ip_lock
    # Check if it's an IP packet
    if IP in packet:
        src_ip = packet[IP].src
        dst_ip = packet[IP].dst
        is_internal_traffic = False
        with peer_ip_lock:
            # Make sure current_peer_ips has been populated before checking
            if current_peer_ips and src_ip in current_peer_ips and dst_ip in current_peer_ips:
                is_internal_traffic = True
        # If it is internal peer traffic, count the bytes
        if is_internal_traffic:
            packet_size = len(packet)
            traffic_key = (src_ip, dst_ip) # Tuple key (source, destination)
            # Update the byte count for this flow direction *under lock*
            with peer_data_lock:
                peer_byte_counts[traffic_key] += packet_size

def start_sniffer(interface, stop_evt):
    """Starts the packet sniffer using Scapy in a background thread."""
    if not NPCAP_AVAILABLE:
        print("Cannot start sniffer: Npcap/Scapy not available or failed to load.")
        stop_evt.set() # Signal main thread to potentially exit if sniffing is critical
        return
    print(f"Attempting to start packet sniffer on interface: {interface or 'default'}...")
    try:
        # sniff is blocking, so it runs until stop_filter returns True
        sniff(iface=interface, prn=packet_handler, store=0, stop_filter=lambda p: stop_evt.is_set(),
              started_callback=lambda: print(f"Packet sniffer started successfully on {interface or scapy_conf.iface}."))
        print("Packet sniffer stopped.")
    except PermissionError:
        print("\nERROR: Permission denied starting sniffer. Try running as Administrator or using 'sudo'.")
        stop_evt.set()
    except OSError as e:
        # Specific errors like "No such device" or Npcap issues
        print(f"\nERROR: OSError starting sniffer on '{interface or 'default'}': {e}")
        print("         Ensure the interface name is correct and Npcap/WinPcap is installed properly.")
        stop_evt.set()
    except Exception as e:
        print(f"\nERROR: Unexpected error starting sniffer: {e}")
        stop_evt.set()

def execute_ping(target_ip):
    """Pings a target IP and returns status and average latency."""
    system = platform.system().lower()
    timeout_ms = PING_TIMEOUT_SECONDS * 1000

    if system == "windows":
        # -n count, -w timeout (milliseconds)
        command = ['ping', '-n', str(PING_COUNT), '-w', str(timeout_ms), target_ip]
        # Example output parsing (adjust if your Windows ping output differs)
        # Reply from 192.168.1.1: bytes=32 time=10ms TTL=64
        # Minimum = 9ms, Maximum = 11ms, Average = 10ms
        success_regex = re.compile(r"Reply from.*time[=<](?P<ms>\d+?)ms")
        avg_regex = re.compile(r"Average = (?P<ms>\d+)ms")
    else: # Linux/macOS
        # -c count, -W timeout (seconds), -i interval (can keep default)
        # Use deadline (-w) as primary timeout mechanism if available, else rely on -W in subprocess
        # Using -W timeout with subprocess.run is generally more reliable
        command = ['ping', '-c', str(PING_COUNT), '-W', str(PING_TIMEOUT_SECONDS), target_ip]
        # Example output parsing (Linux - adjust for macOS if needed)
        # 64 bytes from 192.168.1.1: icmp_seq=1 ttl=64 time=1.23 ms
        # rtt min/avg/max/mdev = 1.10/1.20/1.30/0.10 ms
        success_regex = re.compile(r"bytes from.*time=(?P<ms>\d+\.?\d*) *ms") # Handle float ms
        avg_regex = re.compile(r"min/avg/max/mdev = [\d.]+/([\d.]+)/") # Extract avg

    latency_sum = 0.0
    replies_received = 0
    avg_latency = None
    status = "error" # Default status

    try:
        # Use subprocess.run for better control over timeout and output capture
        result = subprocess.run(command, capture_output=True, text=True, timeout=PING_TIMEOUT_SECONDS + 1) # Add buffer to timeout

        if result.returncode == 0: # Success exit code from ping command
            output = result.stdout
            # Find individual successful pings
            matches = success_regex.findall(output)
            replies_received = len(matches)
            if replies_received > 0:
                 status = "success"
                 # Try to extract average directly if possible
                 avg_match = avg_regex.search(output)
                 if avg_match:
                      avg_latency = float(avg_match.group(1))
                 else:
                     # Fallback: Average the individual times found
                     for ms_str in matches:
                          try:
                              latency_sum += float(ms_str)
                          except ValueError: pass # Ignore if parsing fails
                     if replies_received > 0:
                          avg_latency = round(latency_sum / replies_received, 2)

            else: # Ping command succeeded but no replies (e.g., filtered) - treat as timeout?
                 status = "timeout"

        # Handle specific non-zero return codes if needed (e.g., host unreachable)
        elif result.returncode == 1 and system != "windows": # Often means timeout/unreachable on Linux/Mac
             status = "timeout"
        elif result.returncode != 0 :
             status = "error" # Other errors
             print(f"Ping command failed for {target_ip}. Return Code: {result.returncode}")
             print(f"Stderr: {result.stderr}")


    except subprocess.TimeoutExpired:
        print(f"Ping process timed out for {target_ip}")
        status = "timeout"
    except FileNotFoundError:
        print(f"ERROR: 'ping' command not found. Cannot perform ping tests.")
        status = "error" # Or a specific status?
    except Exception as e:
        print(f"Unexpected error pinging {target_ip}: {e}")
        status = "error"

    # Round latency
    if avg_latency is not None:
        avg_latency = round(avg_latency, 2)

    return {"status": status, "latency_ms": avg_latency}


# --- NEW: Ping Thread Function ---
def ping_targets_periodically(agent_ip, collector_ip, interval, stop_evt):
    """Periodically pings other known peers and the collector."""
    global latest_ping_results, ping_results_lock, current_peer_ips, peer_ip_lock
    print(f"Ping thread started (interval: {interval}s)")

    while not stop_evt.wait(interval):
        if stop_evt.is_set(): break

        targets_to_ping = set()
        # Get current peers under lock
        with peer_ip_lock:
            targets_to_ping.update(current_peer_ips)
        # Add collector IP
        if collector_ip:
             targets_to_ping.add(collector_ip)
        # Remove self from targets
        targets_to_ping.discard(agent_ip)

        if not targets_to_ping:
            # print("Ping: No targets to ping.") # Can be noisy
            continue

        results_this_round = {}
        print(f"Ping: Pinging {len(targets_to_ping)} targets: {targets_to_ping}")
        for target in targets_to_ping:
            ping_result = execute_ping(target)
            results_this_round[target] = {
                "status": ping_result["status"],
                "latency_ms": ping_result["latency_ms"],
                "timestamp": time.time()
            }
            # Optional small sleep between pings to avoid overwhelming network/CPU
            time.sleep(0.05)
            if stop_evt.is_set(): break # Check stop event between pings

        # Update global results under lock
        with ping_results_lock:
            latest_ping_results = results_this_round
            # print(f"Ping results updated: {latest_ping_results}") # Can be noisy

        if stop_evt.is_set(): break

    print("Ping thread stopped.")



# --- Signal Handler ---
def signal_handler(sig, frame):
    """Handles termination signals (Ctrl+C) for graceful shutdown."""
    print('\nSignal received, stopping threads and exiting...')
    stop_event.set() # Signal background threads to stop
    # Give threads a moment to finish
    time.sleep(1)
    sys.exit(0)

# --- Main Script Execution ---
if __name__ == "__main__":
    # --- Initial Setup ---
    agent_ip = get_local_ip()
    if not agent_ip:
        print("ERROR: Could not determine local IP address. Exiting.")
        sys.exit(1)

    collector_ip = get_collector_ip_from_user(agent_ip)
    COLLECTOR_URL = f"http://{collector_ip}:{COLLECTOR_PORT}/data"
    collector_peer_url = f"http://{collector_ip}:{COLLECTOR_PORT}/api/get_peer_ips"
    hostname = socket.gethostname() # Get local hostname

    # --- Get Initial Peer IP List ---
    print("\n--- Initial Peer IP Fetch ---")
    initial_fetch_ok = False
    for attempt in range(5): # Retry a few times initially
        print(f"Attempt {attempt + 1}/5...")
        if get_peer_ips_from_collector(collector_peer_url):
            initial_fetch_ok = True
            break
        else:
            print("  Failed. Retrying in 15 seconds...")
            time.sleep(15)
    if not initial_fetch_ok:
        print("ERROR: Could not fetch initial peer IP list from collector after multiple attempts. Exiting.")
        sys.exit(1)
    print("---------------------------\n")

    # --- Configure Scapy Interface ---
    sniff_iface_name = None # The actual interface name used by Scapy
    if NPCAP_AVAILABLE:
        sniff_iface_name = SNIFF_INTERFACE # Use configured name if provided
        if not sniff_iface_name:
            print("No specific interface configured (SNIFF_INTERFACE). Trying Scapy default...")
            try:
                 # Let scapy pick the default, but show available ones for debugging
                 print("Available interfaces (Scapy view):")
                 scapy_conf.ifaces.show()
                 sniff_iface_name = scapy_conf.iface # Get scapy's default choice
                 print(f"Using Scapy's default interface: {sniff_iface_name}")
            except Exception as e:
                 print(f"ERROR: Could not determine default Scapy interface: {e}")
                 print("       Peer traffic sniffing will be disabled.")
                 NPCAP_AVAILABLE = False # Disable sniffing if we can't get interface

    # --- Start Background Threads ---
    print("\n--- Starting Background Threads ---")
    sniffer_thread = None # Initialize to None
    ip_refresh_thread = None # Initialize to None
    ping_thread = None # <<<< Initialize ping_thread to None >>>>
    if NPCAP_AVAILABLE and sniff_iface_name:
        sniffer_thread = threading.Thread(target=start_sniffer, args=(sniff_iface_name, stop_event), daemon=True)
        sniffer_thread.start()
        print("Sniffer thread started.")
        time.sleep(2) # Give sniffer a moment to start or fail
        # Check if sniffer thread failed immediately
        if stop_event.is_set():
            print("ERROR: Sniffer thread failed to start properly. Exiting.")
            sys.exit(1)
    else:
        print("Skipping packet sniffer thread (Npcap/Scapy unavailable or no interface).")

    ip_refresh_thread = threading.Thread(target=refresh_peer_ips_periodically, args=(collector_peer_url, PEER_IP_REFRESH_INTERVAL_SECONDS, stop_event), daemon=True)
    ip_refresh_thread.start()
    print("IP Refresh thread started.")

    # --- >>> ADDED PING THREAD START <<< ---
    ping_thread = threading.Thread(target=ping_targets_periodically, args=(agent_ip, collector_ip, PING_INTERVAL_SECONDS, stop_event), daemon=True)
    ping_thread.start()
    print("Ping thread started.")
    # --- >>> END ADDED PING THREAD START <<< ---

    print("-------------------------------\n")
    
    def execute_ping(target_ip):
        """Pings a target IP and returns status and average latency."""
        system = platform.system().lower()
        timeout_ms = PING_TIMEOUT_SECONDS * 1000

        if system == "windows":
            # -n count, -w timeout (milliseconds)
            command = ['ping', '-n', str(PING_COUNT), '-w', str(timeout_ms), target_ip]
            # Example output parsing (adjust if your Windows ping output differs)
            # Reply from 192.168.1.1: bytes=32 time=10ms TTL=64
            # Minimum = 9ms, Maximum = 11ms, Average = 10ms
            success_regex = re.compile(r"Reply from.*time[=<](?P<ms>\d+?)ms")
            avg_regex = re.compile(r"Average = (?P<ms>\d+)ms")
        else: # Linux/macOS
            # -c count, -W timeout (seconds), -i interval (can keep default)
            # Use deadline (-w) as primary timeout mechanism if available, else rely on -W in subprocess
            # Using -W timeout with subprocess.run is generally more reliable
            command = ['ping', '-c', str(PING_COUNT), '-W', str(PING_TIMEOUT_SECONDS), target_ip]
            # Example output parsing (Linux - adjust for macOS if needed)
            # 64 bytes from 192.168.1.1: icmp_seq=1 ttl=64 time=1.23 ms
            # rtt min/avg/max/mdev = 1.10/1.20/1.30/0.10 ms
            success_regex = re.compile(r"bytes from.*time=(?P<ms>\d+\.?\d*) *ms") # Handle float ms
            avg_regex = re.compile(r"min/avg/max/mdev = [\d.]+/([\d.]+)/") # Extract avg

        latency_sum = 0.0
        replies_received = 0
        avg_latency = None
        status = "error" # Default status

        try:
            # Use subprocess.run for better control over timeout and output capture
            result = subprocess.run(command, capture_output=True, text=True, timeout=PING_TIMEOUT_SECONDS + 1) # Add buffer to timeout

            if result.returncode == 0: # Success exit code from ping command
                output = result.stdout
                # Find individual successful pings
                matches = success_regex.findall(output)
                replies_received = len(matches)
                if replies_received > 0:
                    status = "success"
                    # Try to extract average directly if possible
                    avg_match = avg_regex.search(output)
                    if avg_match:
                        avg_latency = float(avg_match.group(1))
                    else:
                        # Fallback: Average the individual times found
                        for ms_str in matches:
                            try:
                                latency_sum += float(ms_str)
                            except ValueError: pass # Ignore if parsing fails
                        if replies_received > 0:
                            avg_latency = round(latency_sum / replies_received, 2)

                else: # Ping command succeeded but no replies (e.g., filtered) - treat as timeout?
                    status = "timeout"

            # Handle specific non-zero return codes if needed (e.g., host unreachable)
            elif result.returncode == 1 and system != "windows": # Often means timeout/unreachable on Linux/Mac
                status = "timeout"
            elif result.returncode != 0 :
                status = "error" # Other errors
                print(f"Ping command failed for {target_ip}. Return Code: {result.returncode}")
                print(f"Stderr: {result.stderr}")


        except subprocess.TimeoutExpired:
            print(f"Ping process timed out for {target_ip}")
            status = "timeout"
        except FileNotFoundError:
            print(f"ERROR: 'ping' command not found. Cannot perform ping tests.")
            status = "error" # Or a specific status?
        except Exception as e:
            print(f"Unexpected error pinging {target_ip}: {e}")
            status = "error"

        # Round latency
        if avg_latency is not None:
            avg_latency = round(avg_latency, 2)

        return {"status": status, "latency_ms": avg_latency}


    # --- NEW: Ping Thread Function ---
    def ping_targets_periodically(agent_ip, collector_ip, interval, stop_evt):
        """Periodically pings other known peers and the collector."""
        global latest_ping_results, ping_results_lock, current_peer_ips, peer_ip_lock
        print(f"Ping thread started (interval: {interval}s)")

        while not stop_evt.wait(interval):
            if stop_evt.is_set(): break

            targets_to_ping = set()
            # Get current peers under lock
            with peer_ip_lock:
                targets_to_ping.update(current_peer_ips)
            # Add collector IP
            if collector_ip:
                targets_to_ping.add(collector_ip)
            # Remove self from targets
            targets_to_ping.discard(agent_ip)

            if not targets_to_ping:
                # print("Ping: No targets to ping.") # Can be noisy
                continue

            results_this_round = {}
            print(f"Ping: Pinging {len(targets_to_ping)} targets: {targets_to_ping}")
            for target in targets_to_ping:
                ping_result = execute_ping(target)
                results_this_round[target] = {
                    "status": ping_result["status"],
                    "latency_ms": ping_result["latency_ms"],
                    "timestamp": time.time()
                }
                # Optional small sleep between pings to avoid overwhelming network/CPU
                time.sleep(0.05)
                if stop_evt.is_set(): break # Check stop event between pings

            # Update global results under lock
            with ping_results_lock:
                latest_ping_results = results_this_round
                # print(f"Ping results updated: {latest_ping_results}") # Can be noisy

            if stop_evt.is_set(): break

        print("Ping thread stopped.")


    # --- Register Signal Handler ---
    signal.signal(signal.SIGINT, signal_handler) # Handle Ctrl+C
    signal.signal(signal.SIGTERM, signal_handler) # Handle termination signals

    # --- Main Reporting Loop ---
    last_report_time = time.time()
    # Initialize counters *before* the loop
    last_net_counters = {}
    last_disk_counters = {}
    try:
        # Get initial counters to calculate deltas on the *first* real report
        last_net_counters = psutil.net_io_counters(pernic=True)
        # *** INITIALIZE DISK COUNTERS ***
        last_disk_counters = psutil.disk_io_counters(perdisk=True)
        # Call cpu_percent once initially to start measurement period
        psutil.cpu_percent(interval=None)
        print("Initial psutil counters obtained.")
    except Exception as e:
         print(f"Warning: Failed to get initial psutil counters: {e}")
         # Keep counters as empty dicts if initial fetch fails; calculations will skip first cycle

    print(f"\nStarting agent on {hostname} ({agent_ip})")
    print(f"Reporting data to {COLLECTOR_URL} every {REPORT_INTERVAL_SECONDS} seconds...")
    print("Press Ctrl+C to stop.")

    try:
        while not stop_event.is_set():
            current_time = time.time()
            # Calculate precise time elapsed since last *successful* report time
            time_delta = current_time - last_report_time
            # Wait efficiently until the next report interval is due
            time_to_next_interval = REPORT_INTERVAL_SECONDS - time_delta
            if time_to_next_interval > 0:
                stop_event.wait(timeout=time_to_next_interval)
                # Check if stop_event was set during the wait
                if stop_event.is_set():
                    break

            # Recalculate actual time delta after waiting/running
            current_time = time.time()
            time_delta = current_time - last_report_time
            # Avoid division by zero or tiny deltas causing huge rates
            if time_delta < 0.1:
                # print("Warning: Time delta too small, skipping report cycle.")
                continue

            # --- Collect Peer Traffic Data ---
            peer_traffic_this_interval = {}
            if NPCAP_AVAILABLE:
                with peer_data_lock:
                    # Atomically copy and clear the counters for this interval
                    current_peer_counts = peer_byte_counts.copy()
                    peer_byte_counts.clear()
                # Calculate rates based on the actual time delta
                for (src, dst), byte_count in current_peer_counts.items():
                    mbps = round((byte_count * 8) / (time_delta * 1024 * 1024), 3)
                    peer_traffic_this_interval[f"{src}_to_{dst}"] = {"bytes": byte_count, "Mbps": mbps}

            # --- Collect Standard psutil Stats ---
            cpu_stats = get_cpu_stats()
            memory_stats = get_memory_stats()
            disk_usage = get_disk_usage_stats(DISKS_TO_MONITOR_USAGE)

            # --- Calculate PSUTIL Network Interface Stats ---
            network_data = {
                "total": {"sent_Mbps": 0.0, "recv_Mbps": 0.0, "throughput_Mbps": 0.0},
                "interfaces": {},
                "reported_total_link_speed_mbps": 0
            }
            current_net_counters = {} # Holds counters for this cycle
            try:
                current_net_counters = psutil.net_io_counters(pernic=True)
                current_if_stats = psutil.net_if_stats() # Get stats like speed, isup

                # Check if we have previous counters to calculate delta
                if last_net_counters:
                    total_sent_bps = 0.0
                    total_recv_bps = 0.0
                    total_active_link_speed = 0

                    # Determine which interfaces to process based on config or defaults
                    interfaces_to_process = []
                    all_nics = list(current_net_counters.keys())
                    specified_interfaces = INTERFACES_TO_MONITOR

                    if specified_interfaces is None: # Monitor all non-virtual/loopback
                        for nic in all_nics:
                            lname = nic.lower()
                            # Add more filters as needed
                            if 'loopback' in lname or 'pseudo' in lname or ' lo' in lname or 'vmnet' in lname or 'virtual' in lname:
                                continue
                            if nic in current_if_stats: # Only include nics with stats
                                interfaces_to_process.append(nic)
                    else: # Monitor only specified interfaces that actually exist now
                        interfaces_to_process = [nic for nic in specified_interfaces if nic in current_net_counters and nic in current_if_stats]

                    # Calculate deltas for the selected interfaces
                    for nic in interfaces_to_process:
                        if nic not in last_net_counters: continue # Skip if no previous data

                        current_counts = current_net_counters[nic]
                        last_counts = last_net_counters[nic]
                        nic_stats = current_if_stats.get(nic) # nic guaranteed to be in current_if_stats now
                        is_up = nic_stats.isup
                        link_speed_mbps = nic_stats.speed if nic_stats else 0

                        # Only calculate rates for interfaces that are UP
                        if is_up:
                            bytes_sent_delta = max(0, current_counts.bytes_sent - last_counts.bytes_sent)
                            bytes_recv_delta = max(0, current_counts.bytes_recv - last_counts.bytes_recv)
                            sent_bps = (bytes_sent_delta / time_delta)
                            recv_bps = (bytes_recv_delta / time_delta)

                            total_sent_bps += sent_bps
                            total_recv_bps += recv_bps
                            # Sum link speed only for active interfaces being monitored
                            total_active_link_speed += link_speed_mbps

                            # Calculate link utilization percentage
                            sent_percent = round((sent_bps * 8) / (link_speed_mbps * 1000 * 1000) * 100, 2) if link_speed_mbps > 0 else -1.0
                            recv_percent = round((recv_bps * 8) / (link_speed_mbps * 1000 * 1000) * 100, 2) if link_speed_mbps > 0 else -1.0

                            # Store detailed stats for this interface
                            network_data["interfaces"][nic] = {
                                 "is_up": is_up,
                                 "link_speed_mbps": link_speed_mbps,
                                 "sent_Mbps": round(sent_bps * 8 / (1024*1024), 2),
                                 "recv_Mbps": round(recv_bps * 8 / (1024*1024), 2),
                                 "sent_percent_of_link": sent_percent,
                                 "recv_percent_of_link": recv_percent,
                            }
                    # --- End interface loop ---

                    # Store aggregated totals
                    network_data["total"]["sent_Mbps"] = round(total_sent_bps * 8 / (1024*1024), 2)
                    network_data["total"]["recv_Mbps"] = round(total_recv_bps * 8 / (1024*1024), 2)
                    network_data["total"]["throughput_Mbps"] = round((total_sent_bps + total_recv_bps) * 8 / (1024*1024), 2)
                    network_data["reported_total_link_speed_mbps"] = total_active_link_speed

            except Exception as e:
                print(f"\nWarning: Error calculating psutil network stats: {e}")
                # Keep network_data as default empty/zero values if calc fails

            # --- Calculate Disk I/O Rates ---
            disk_io_rates = {}
            current_disk_counters = {} # Holds counters for this cycle
            try:
                # Get current counters for all physical disks
                current_disk_counters = psutil.disk_io_counters(perdisk=True)

                # Check if we have previous counters to calculate delta
                if last_disk_counters and current_disk_counters:
                    for disk, current_counts in current_disk_counters.items():
                         # Ensure we have previous data for this specific disk
                         if disk in last_disk_counters:
                            last_counts = last_disk_counters[disk]

                            # Calculate deltas (bytes and operation counts)
                            read_bytes_delta = max(0, current_counts.read_bytes - last_counts.read_bytes)
                            write_bytes_delta = max(0, current_counts.write_bytes - last_counts.write_bytes)
                            read_count_delta = max(0, current_counts.read_count - last_counts.read_count)
                            write_count_delta = max(0, current_counts.write_count - last_counts.write_count)

                            # Calculate rates per second
                            read_bps = read_bytes_delta / time_delta  # Bytes per second
                            write_bps = write_bytes_delta / time_delta # Bytes per second
                            read_ops_ps = read_count_delta / time_delta   # Reads per second (IOPS)
                            write_ops_ps = write_count_delta / time_delta  # Writes per second (IOPS)

                            # Store the calculated rates for this disk
                            disk_io_rates[disk] = {
                                 "read_Bps": round(read_bps, 2),
                                 "write_Bps": round(write_bps, 2),
                                 "read_ops_ps": round(read_ops_ps, 2),
                                 "write_ops_ps": round(write_ops_ps, 2)
                                 # Optional: could add MB/s here too if needed
                                 # "read_MBps": round(read_bps / (1024*1024), 2),
                                 # "write_MBps": round(write_bps / (1024*1024), 2),
                             }
            except Exception as e:
                 print(f"\nWarning: Error calculating disk I/O stats: {e}")
                 # Keep disk_io_rates empty if calculation fails
            
            ping_data = {}
            with ping_results_lock:
                 # Make a copy to avoid holding lock during payload assembly
                 ping_data = latest_ping_results.copy()

            # --- Assemble Payload ---
            payload = {
                "hostname": hostname,
                "agent_ip": agent_ip,
                "timestamp_utc": datetime.datetime.utcnow().isoformat() + "Z",
                "interval_sec": round(time_delta, 2),
                "cpu": cpu_stats,
                "memory": memory_stats,
                "disk_usage": disk_usage, # Disk space utilization
                "network": network_data,   # Network throughput/utilization
                "disk_io": disk_io_rates,  # Disk IOPS and Bps (NEW)
                "peer_traffic": peer_traffic_this_interval, # Peer traffic from sniffer
                "ping_results": ping_data
            }

            # --- Send Data ---
            try:
                # print(f"Sending payload: {json.dumps(payload, indent=2)}") # Verbose debug
                response = requests.post(COLLECTOR_URL, json=payload, timeout=(3, 10)) # (connect, read)
                response.raise_for_status() # Check for HTTP errors
                # Indicate success with a dot
                sys.stdout.write('.'); sys.stdout.flush()
            except requests.exceptions.Timeout: print("T", end='', flush=True) # Timeout sending data
            except requests.exceptions.ConnectionError: print("C", end='', flush=True) # Connection error sending data
            except requests.exceptions.RequestException as e: print(f"E{getattr(e.response, 'status_code', 'N')}", end='', flush=True) # Other Request error
            except Exception as e: print(f"X", end='', flush=True) # Unexpected send error

            # --- Update state for next iteration ---
            last_report_time = current_time
            # Update counters *only if* they were successfully read this cycle
            if current_net_counters:
                 last_net_counters = current_net_counters
            # *** UPDATE DISK COUNTERS ***
            if current_disk_counters:
                 last_disk_counters = current_disk_counters

    # --- Exception Handling and Finalization ---
    except KeyboardInterrupt:
        print("\nCtrl+C detected, stopping...")
    except Exception as e:
        print(f"\nCRITICAL ERROR in main loop: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("\nShutting down agent...")
        stop_event.set() # Signal threads to stop
        # Wait briefly for threads to potentially finish
        if sniffer_thread and sniffer_thread.is_alive():
            print("Waiting for sniffer thread...")
            sniffer_thread.join(timeout=1.0)
        if ip_refresh_thread and ip_refresh_thread.is_alive():
            print("Waiting for IP refresh thread...")
            ip_refresh_thread.join(timeout=1.0)
        # --- Now ping_thread should be defined if started ---
        if ping_thread and ping_thread.is_alive(): # Check if it was created and is running
            print("Waiting for ping thread...")
            ping_thread.join(timeout=1.0)
        print("Agent finished.")