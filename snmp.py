import sys
import argparse
import time
from pysnmp.hlapi import (
    SnmpEngine, CommunityData, UdpTransportTarget, ContextData, ObjectType,
    ObjectIdentity, getCmd, nextCmd
)
# --- Define OIDs ---
# System Info
OID_SYS_DESCR = '1.3.6.1.2.1.1.1.0'
OID_SYS_UPTIME = '1.3.6.1.2.1.1.3.0' # TimeTicks since last re-initialization

# Interface Info
OID_IF_DESCR = '1.3.6.1.2.1.2.2.1.2' # Interface names/descriptions (Table Entry)
OID_IF_TABLE_ENTRY = '1.3.6.1.2.1.2.2.1' # Base OID for the entire ifEntry table

# Interface Traffic Counters (Octets = Bytes)
# 64-bit (preferred for high speed interfaces to avoid rollover)
OID_IF_HC_IN_OCTETS = '1.3.6.1.2.1.31.1.1.1.6'
OID_IF_HC_OUT_OCTETS = '1.3.6.1.2.1.31.1.1.1.10'
# 32-bit (fallback if 64-bit not supported)
OID_IF_IN_OCTETS = '1.3.6.1.2.1.2.2.1.10'
OID_IF_OUT_OCTETS = '1.3.6.1.2.1.2.2.1.16'

# --- Helper Functions ---

def format_uptime(timeticks_obj):
    """ Converts SNMP TimeTicks object to human-readable format """
    if timeticks_obj is None:
        return "N/A"
    try:
        total_seconds = int(timeticks_obj) / 100
        days, remainder = divmod(total_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{int(days)}d {int(hours)}h {int(minutes)}m {int(seconds)}s"
    except Exception as e:
        # print(f"Debug format_uptime error: {e}") # Uncomment for debugging
        return "Error formatting"

def snmp_get_values(engine, auth_data, transport_target, oids):
    """ Performs SNMP GET for a list of OIDs """
    results = {}
    try:
        iterator = getCmd(
            engine,
            auth_data,
            transport_target,
            ContextData(),
            *[ObjectType(ObjectIdentity(oid)) for oid in oids] # Unpack OIDs
        )

        errorIndication, errorStatus, errorIndex, varBinds = next(iterator)

        if errorIndication:
            print(f"  ERROR [GET]: {errorIndication}")
            return None
        elif errorStatus:
            print(f'  ERROR [GET]: {errorStatus.prettyPrint()} at '
                  f'{errorIndex and varBinds[int(errorIndex) - 1][0] or "?"}')
            return None
        else:
            for oid_obj, val_obj in varBinds:
                results[str(oid_obj)] = val_obj # Store result with OID as string key
            return results

    except Exception as e:
        print(f"  ERROR [GET]: Unexpected error during SNMP GET: {e}")
        return None


def snmp_walk_table(engine, auth_data, transport_target, table_base_oid):
    """ Performs SNMP WALK for a table OID """
    results = {}
    try:
        # Use nextCmd (GETNEXT) to walk the table
        for (errorIndication,
             errorStatus,
             errorIndex,
             varBinds) in nextCmd(engine,
                                  auth_data,
                                  transport_target,
                                  ContextData(),
                                  ObjectType(ObjectIdentity(table_base_oid)),
                                  lexicographicMode=False): # Important for walking tables

            if errorIndication:
                print(f"  ERROR [WALK]: {errorIndication}")
                return None # Stop walk on error
            elif errorStatus:
                print(f'  ERROR [WALK]: {errorStatus.prettyPrint()} at '
                      f'{errorIndex and varBinds[int(errorIndex) - 1][0] or "?"}')
                return None # Stop walk on error
            else:
                # varBinds contains list of (OID, value) tuples for this step
                for oid_obj, val_obj in varBinds:
                    # Check if the returned OID is still within the table we're walking
                    if not ObjectIdentity(table_base_oid).isPrefixOf(oid_obj):
                         # We've walked past the end of the table
                         return results # Walk complete
                    # Store result: OID string -> value object
                    results[str(oid_obj)] = val_obj
        # If loop finishes without error, return collected results
        return results

    except Exception as e:
        print(f"  ERROR [WALK]: Unexpected error during SNMP WALK for {table_base_oid}: {e}")
        return None


# --- Main Script ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Basic SNMP Checker Tool using pysnmp")
    parser.add_argument("hostname", help="IP address or hostname of the SNMP device")
    parser.add_argument("-c", "--community", default="public", help="SNMP community string (default: public)")
    parser.add_argument("-v", "--version", choices=['1', '2'], default='2', help="SNMP version (1 or 2 for 2c) (default: 2)")
    parser.add_argument("-t", "--timeout", type=int, default=2, help="SNMP timeout in seconds (default: 2)")
    parser.add_argument("-r", "--retries", type=int, default=1, help="SNMP retries (default: 1)")

    args = parser.parse_args()

    # Map version arg to pysnmp's mpModel (0 for v1, 1 for v2c)
    mp_model = 0 if args.version == '1' else 1

    print(f"--- SNMP Check for {args.hostname} ---")
    print(f"Community: '{args.community}', Version: {args.version}c, Timeout: {args.timeout}s, Retries: {args.retries}")
    print("-" * 30)

    # Create SNMP Engine instance
    snmp_engine = SnmpEngine()

    # Define authentication data
    auth_data = CommunityData(args.community, mpModel=mp_model)

    # Define transport target
    transport_target = UdpTransportTarget((args.hostname, 161), timeout=args.timeout, retries=args.retries)

    # 1. Check System Description & Uptime
    print("[1] Checking System Description & Uptime...")
    sys_info = snmp_get_values(snmp_engine, auth_data, transport_target, [OID_SYS_DESCR, OID_SYS_UPTIME])
    if sys_info:
        descr_obj = sys_info.get(OID_SYS_DESCR)
        uptime_obj = sys_info.get(OID_SYS_UPTIME)
        print(f"  System Description: {descr_obj.prettyPrint() if descr_obj else 'N/A'}")
        print(f"  System Uptime: {format_uptime(uptime_obj)}")
    else:
        print("  Failed to get system info. Halting further checks.")
        sys.exit(1) # Exit if basic info fails

    print("-" * 30)
    time.sleep(0.5) # Small pause

    # 2. Check Interface Descriptions
    print("[2] Checking Interface Descriptions (ifDescr)...")
    # Walk the ifDescr table specifically
    interfaces = snmp_walk_table(snmp_engine, auth_data, transport_target, OID_IF_DESCR)
    interface_map = {} # Store index -> name mapping
    if interfaces:
        print("  Interfaces Found:")
        for oid_str, name_obj in interfaces.items():
            try:
                # OID format is like 1.3.6...ifDescr.INDEX
                if_index = int(oid_str.split('.')[-1])
                if_name = name_obj.prettyPrint() # Use prettyPrint for displayable string
                interface_map[if_index] = if_name
                print(f"    Index {if_index}: {if_name}")
            except (ValueError, IndexError):
                print(f"    Warning: Could not parse index/name for OID {oid_str}")
        if not interface_map:
             print("  No valid interface descriptions found.")
    else:
        print("  Failed to get interface descriptions.")

    print("-" * 30)
    time.sleep(0.5)

    # 3. Check Traffic Counters (Attempt 64-bit first)
    print("[3] Checking Traffic Counters (Attempting 64-bit: ifHCOctets)...")
    counters_in_64 = snmp_walk_table(snmp_engine, auth_data, transport_target, OID_IF_HC_IN_OCTETS)
    counters_out_64 = snmp_walk_table(snmp_engine, auth_data, transport_target, OID_IF_HC_OUT_OCTETS)

    got_64bit = False
    in_counters = {}
    out_counters = {}

    if counters_in_64 is not None and counters_out_64 is not None:
        # Check if we actually got non-empty results
        if len(counters_in_64) > 0 or len(counters_out_64) > 0:
             print("  64-bit Counters Found (ifHCOctets):")
             got_64bit = True
             # Process and store (index -> value string)
             in_counters = {int(oid.split('.')[-1]): val.prettyPrint() for oid, val in counters_in_64.items()}
             out_counters = {int(oid.split('.')[-1]): val.prettyPrint() for oid, val in counters_out_64.items()}
        else:
             print("  Query for 64-bit counters succeeded but returned no data (Device might not support them).")
    else:
        print("  Failed to get 64-bit counters (or timed out during query).")


    # 4. Check Traffic Counters (Fallback to 32-bit if 64-bit failed/empty)
    if not got_64bit:
        print("-" * 30)
        print("[4] Checking Traffic Counters (Fallback: 32-bit ifOctets)...")
        counters_in_32 = snmp_walk_table(snmp_engine, auth_data, transport_target, OID_IF_IN_OCTETS)
        counters_out_32 = snmp_walk_table(snmp_engine, auth_data, transport_target, OID_IF_OUT_OCTETS)

        if counters_in_32 is not None and counters_out_32 is not None:
             if len(counters_in_32) > 0 or len(counters_out_32) > 0:
                 print("  32-bit Counters Found (ifOctets):")
                 # Overwrite/populate counter dicts with 32-bit results
                 in_counters = {int(oid.split('.')[-1]): val.prettyPrint() for oid, val in counters_in_32.items()}
                 out_counters = {int(oid.split('.')[-1]): val.prettyPrint() for oid, val in counters_out_32.items()}
                 got_counters = True # Mark that we got *some* counters
             else:
                 print("  Query for 32-bit counters succeeded but returned no data.")
                 got_counters = False
        else:
            print("  Failed to get 32-bit counters as well.")
            got_counters = False
    else:
        got_counters = True # We already got 64-bit ones

    # 5. Display Combined Counter Results
    print("-" * 30)
    print("[5] Traffic Counter Summary:")
    if got_counters and interface_map:
         for index, name in interface_map.items():
             in_val = in_counters.get(index, 'N/A')
             out_val = out_counters.get(index, 'N/A')
             print(f"    Index {index} ({name}): In={in_val}, Out={out_val}")
    elif not interface_map:
         print("  Cannot display counters as no interface names were found.")
    else:
         print("  No traffic counters could be retrieved.")


    print("-" * 30)
    print("--- Check Complete ---")