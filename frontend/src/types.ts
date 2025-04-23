// src/types.ts
export interface NetworkAdapter {
  link_speed_mbps: number;
  utilization_percent: number;
  is_up: boolean; // <-- ADD THIS LINE
  sent_Mbps?: number;
  recv_Mbps?: number;
  is_choked: boolean;
}

export interface AlertInfo {
  id: string; // Unique ID (e.g., toastId format)
  type: 'warning' | 'info' | 'error'; // Type of alert
  message: string;
  timestamp: number; // When it occurred
  hostname: string;
  interfaceName: string;
  acknowledged?: boolean; // Optional: If user can dismiss/ack
}

export interface DiskIoStats {
  read_Bps?: number;
  write_Bps?: number;
  read_ops_ps?: number;
  write_ops_ps?: number;
}


export interface DiskInfo {
  free_gb: number;
  percent: number;
  total_gb: number;
}

export interface PeerFlow {
  Mbps: number;
}

export interface SystemData {
  agent_ip: string;
  cpu_percent: number;
  disks: Record<string, DiskInfo>; // Disk Usage
  hostname: string;
  last_seen_relative: string;
  mem_percent: number;
  network_adapters: Record<string, NetworkAdapter>;
  peer_traffic: Record<string, PeerFlow>;
  recv_mbps: number;
  sent_mbps: number;
  timestamp_utc: string;
  total_nic_speed_mbps: number;
  total_throughput_mbps: number;
  // --- ADD THIS LINE ---
  disk_io?: Record<string, DiskIoStats>; // Optional: Disk I/O rates (IOPS, Bps)
  history_slice?: {
    cpu: (number | null)[];
    mem: (number | null)[];
    // Add other slices here if the backend provides them (e.g., network)
  };
  has_alert?: boolean;
  // --- END ADDITION ---
}

export interface HostData {
  data: SystemData;
  last_seen: number;
}

export type SortKey = 'hostname' | 'cpu_percent' | 'mem_percent' | 'total_throughput_mbps' |'disk_total_iops';
export type SortOrder = 'asc' | 'desc';

export interface HostCardProps {
  hostname: string;
  data: SystemData;
  onSelect: () => void;
  sortConfig: {
    key: SortKey;
    order: SortOrder;
  };
  onSort: (key: SortKey) => void;
}


export interface HostDetailModalProps {
  hostname: string;
  data: SystemData;
  onClose: () => void;
}
