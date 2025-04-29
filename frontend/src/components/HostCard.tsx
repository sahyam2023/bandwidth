// src/components/HostCard.tsx
import { FC } from 'react';
import { ArrowUpDown, ArrowUp, ArrowDown, Server, HardDrive } from 'lucide-react';
import ProgressBar from './ProgressBar';
import { HostCardProps, SortKey, PeerFlow, DiskIoStats } from '../types'; 
import React from 'react'; 
import { Sparklines, SparklinesLine } from 'react-sparklines';


// findTopPeer function (Keep as is)
const findTopPeer = (peerTraffic: Record<string, PeerFlow>, agentIp: string) => {
  // ... implementation ...
  let topFlow: [string, PeerFlow] | null = null;
  let maxRate = -1;
  let direction: 'inbound' | 'outbound' | null = null;
  let peerIp: string | null = null;
  for (const [key, flow] of Object.entries(peerTraffic || {})) {
    const rate = flow?.Mbps ?? 0;
    if (rate > maxRate) {
      const parts = key.split('_to_');
      if (parts.length === 2) {
        const [src, dst] = parts;
        if (src === agentIp) { direction = 'outbound'; peerIp = dst; }
        else if (dst === agentIp) { direction = 'inbound'; peerIp = src; }
        else { continue; }
        maxRate = rate;
        topFlow = [key, flow];
      }
    }
  }
  return topFlow ? { key: topFlow[0], data: topFlow[1], rate: maxRate, direction, peerIp } : null;
};

const HostCard: FC<HostCardProps> = ({ hostname, data, onSelect, sortConfig, onSort }) => {

  const topOverallPeer = findTopPeer(data.peer_traffic, data.agent_ip);

  // Calculate Total Disk IOPS (Keep your calculation logic here)
  let totalReadIops = 0;
  let totalWriteIops = 0;
  let hasDiskIoData = false;
  const diskIoData: Record<string, DiskIoStats> | undefined = data?.disk_io; // Use correct type
  if (diskIoData && typeof diskIoData === 'object' && Object.keys(diskIoData).length > 0) {
    hasDiskIoData = true;
    Object.values(diskIoData).forEach((diskStats: DiskIoStats) => {
      if (diskStats && typeof diskStats === 'object') {
        totalReadIops += diskStats.read_ops_ps ?? 0;
        totalWriteIops += diskStats.write_ops_ps ?? 0;
      }
    });
  }
  const formattedDiskIo = hasDiskIoData
    ? `R: ${totalReadIops.toFixed(1)} / W: ${totalWriteIops.toFixed(1)} IOPS`
    : 'N/A';
  // Keep total for sorting comparison if needed in sortHosts utility
  // const diskTotalIopsForSort = hasDiskIoData ? totalReadIops + totalWriteIops : -1;


  // Helper Function to Get Sort Icon
  const getSortIcon = (key: SortKey) => {
    if (sortConfig.key !== key) { return ArrowUpDown; }
    return sortConfig.order === 'asc' ? ArrowUp : ArrowDown;
  };

  // Helper Function to Check if Sorting by Key
  const isSortingByKey = (key: SortKey): boolean => {
    return sortConfig.key === key;
  };

  const cpuHistory = data?.history_slice?.cpu?.map(v => v ?? 0) ?? []; // Default nulls to 0 for chart
  const memHistory = data?.history_slice?.mem?.map(v => v ?? 0) ?? []; // Default nulls to 0 for chart
  const hasCpuHistory = cpuHistory.length > 1; // Need at least 2 points for a line
  const hasMemHistory = memHistory.length > 1;


  return (
    <div
      onClick={onSelect}
      className="bg-white dark:bg-gray-800 rounded-xl shadow-sm hover:shadow-md transition-shadow cursor-pointer border border-gray-200 dark:border-gray-700 overflow-hidden"
    >
      <div className="p-4 sm:p-5">
        <div className="flex items-start justify-between mb-4">
          <div>
            <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100 flex items-center gap-2">
              <Server className="w-5 h-5 text-blue-500" />
              {hostname}
            </h3>
            <p className="text-sm text-gray-500 dark:text-gray-400">{data.agent_ip}</p>
          </div>
          <span className="text-xs text-gray-400 dark:text-gray-500">{data.last_seen_relative}</span>
        </div>

        <div className="space-y-3 sm:space-y-4">
          {/* CPU Section */}
          <div className="space-y-1">
            <div className="flex justify-between items-center">
              <span className="text-sm font-medium text-gray-600 dark:text-gray-300">CPU</span>
              <span className="text-xs font-semibold text-gray-700 dark:text-gray-200">
                 {data.cpu_percent >= 0 ? `${data.cpu_percent.toFixed(1)}%` : 'N/A'}
              </span>
              <button
                onClick={(e) => { e.stopPropagation(); onSort('cpu_percent'); }}
                className={`p-1 rounded-md text-gray-400 hover:text-gray-700 hover:bg-gray-100 dark:hover:bg-gray-700 ${isSortingByKey('cpu_percent') ? 'text-blue-500 bg-blue-50 dark:bg-blue-900/50' : 'dark:text-gray-500 dark:hover:text-gray-300'}`}
                aria-label="Sort by CPU"
              >
                {React.createElement(getSortIcon('cpu_percent'), { className: "w-4 h-4" })}
              </button>
            </div>
            <div className="h-6"> {/* Set fixed height container */}
               {hasCpuHistory ? (
                <Sparklines data={cpuHistory} svgWidth={100} svgHeight={24} margin={2}>
                     <SparklinesLine color="rgb(59, 130, 246)" style={{ fill: "none", strokeWidth: 1.5 }} />
                </Sparklines>
               ) : (
                 // Fallback to ProgressBar
                 <ProgressBar value={data.cpu_percent} small />
               )}
            </div>
          </div>

          {/* Memory Section */}
          <div className="space-y-1">
            <div className="flex justify-between items-center">
              <span className="text-sm font-medium text-gray-600 dark:text-gray-300">Memory</span>
              <span className="text-xs font-semibold text-gray-700 dark:text-gray-200">
                 {data.mem_percent >= 0 ? `${data.mem_percent.toFixed(1)}%` : 'N/A'}
               </span>
              <button
                onClick={(e) => { e.stopPropagation(); onSort('mem_percent'); }}
                className={`p-1 rounded-md text-gray-400 hover:text-gray-700 hover:bg-gray-100 dark:hover:bg-gray-700 ${isSortingByKey('mem_percent') ? 'text-blue-500 bg-blue-50 dark:bg-blue-900/50' : 'dark:text-gray-500 dark:hover:text-gray-300'}`}
                aria-label="Sort by Memory"
              >
                {React.createElement(getSortIcon('mem_percent'), { className: "w-4 h-4" })}
              </button>
            </div>
            <div className="h-6"> {/* Set fixed height container */}
               {hasMemHistory ? (
                 <Sparklines data={memHistory} svgWidth={100} svgHeight={24} margin={2}>
                     <SparklinesLine color="rgb(234, 179, 8)" style={{ fill: "none", strokeWidth: 1.5 }} />
                 </Sparklines>
               ) : (
                 // Fallback to ProgressBar
                 <ProgressBar value={data.mem_percent} small />
               )}
            </div>
            {/* --- END MODIFIED BLOCK --- */}
          </div>

          {/* Network Section */}
          <div className="space-y-1">
            <div className="flex justify-between items-center">
              <span className="text-sm font-medium text-gray-600 dark:text-gray-300">Network Total</span>
              <span className="text-xs font-semibold text-gray-700 dark:text-gray-200">
                 {data.total_throughput_mbps >= 0 ? `${data.total_throughput_mbps.toFixed(1)} Mbps` : 'N/A'}
              </span>
              <button
                onClick={(e) => { e.stopPropagation(); onSort('total_throughput_mbps'); }}
                className={`p-1 rounded-md text-gray-400 hover:text-gray-700 hover:bg-gray-100 dark:hover:bg-gray-700 ${isSortingByKey('total_throughput_mbps') ? 'text-blue-500 bg-blue-50 dark:bg-blue-900/50' : 'dark:text-gray-500 dark:hover:text-gray-300'}`}
                aria-label="Sort by Network Throughput"
              >
                {React.createElement(getSortIcon('total_throughput_mbps'), { className: "w-4 h-4" })}
              </button>
            </div>

          </div>

          {/* Disk IOPS Section */}
          <div className="space-y-1">
            <div className="flex justify-between items-center">
              <span className="text-sm font-medium text-gray-600 dark:text-gray-300 flex items-center gap-1">
                <HardDrive className="w-3 h-3 text-gray-400 dark:text-gray-500" /> Disk IOPS
              </span>
              <span className="text-xs font-semibold text-gray-700 dark:text-gray-200">
                 {formattedDiskIo}
              </span>
              <button
                onClick={(e) => { e.stopPropagation(); onSort('disk_total_iops'); }}
                className={`p-1 rounded-md text-gray-400 hover:text-gray-700 hover:bg-gray-100 dark:hover:bg-gray-700 ${isSortingByKey('disk_total_iops') ? 'text-blue-500 bg-blue-50 dark:bg-blue-900/50' : 'dark:text-gray-500 dark:hover:text-gray-300'}`}
                aria-label="Sort by Disk IOPS"
              >
                {React.createElement(getSortIcon('disk_total_iops'), { className: "w-4 h-4" })}
              </button>
            </div>
          </div>

          {/* Peer Traffic Section */}
          <div className="space-y-1">
          <div className="flex justify-between items-baseline">
               <span className="text-sm font-medium text-gray-600 dark:text-gray-300">Top Peer Flow</span>
               {topOverallPeer ? (
                 <span className="text-xs text-gray-700 dark:text-gray-200">
                    {topOverallPeer.direction === 'outbound' ? '→' : '←'} {topOverallPeer.peerIp}: {topOverallPeer.rate.toFixed(2)} Mbps
                 </span>
               ) : (
                 <span className="text-xs text-gray-400 dark:text-gray-500 italic">None</span>
               )}
             </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default HostCard;