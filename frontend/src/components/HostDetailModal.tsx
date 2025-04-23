// src/components/HostDetailModal.tsx
import React, { FC, useCallback, useEffect, useState, useMemo } from 'react';
import { X, ArrowUp, ArrowDown, ArrowUpDown, AlertTriangle, HardDrive } from 'lucide-react';
import ProgressBar from './ProgressBar';
import { HostDetailModalProps, NetworkAdapter, DiskInfo, DiskIoStats } from '../types';

// --- Charting Imports ---
import { Line } from 'react-chartjs-2';
import {
  Chart as ChartJS,
  LinearScale, TimeScale, PointElement, LineElement, Title, Tooltip, Legend, Filler
} from 'chart.js';
import 'chartjs-adapter-date-fns';
import { parseISO } from 'date-fns';

// --- Register Chart.js components ---
ChartJS.register(LinearScale, TimeScale, PointElement, LineElement, Title, Tooltip, Legend, Filler);

// --- Types ---
interface TransformedPeerData { key: string; direction: 'inbound' | 'outbound'; peerIp: string; rateMbps: number; }
type PeerSortKey = 'peerIp' | 'rateMbps';
type PeerSortOrder = 'asc' | 'desc';
interface HistoryData {
  timestamps: string[]; cpu_percent: (number | null)[]; mem_percent: (number | null)[];
  network_interfaces: { [iface: string]: { sent_Mbps: (number | null)[]; recv_Mbps: (number | null)[]; } };
  latest_link_speeds: { [iface: string]: number; }
}
const formatBytesPerSecond = (bytes: number | null | undefined): string => {
  if (bytes === null || typeof bytes === 'undefined' || bytes < 0) return 'N/A';
  if (bytes === 0) return '0 B/s';
  const k = 1024;
  const sizes = ['B/s', 'KB/s', 'MB/s', 'GB/s', 'TB/s'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
};
// --- Component ---
const HostDetailModal: FC<HostDetailModalProps> = ({ hostname, data, onClose }) => {
  // --- State ---
  const [peerSortConfig, setPeerSortConfig] = useState<{ key: PeerSortKey; order: PeerSortOrder }>({ key: 'rateMbps', order: 'desc' });
  const [historyData, setHistoryData] = useState<HistoryData | null>(null);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyError, setHistoryError] = useState<string | null>(null);

  // --- Callbacks & Effects ---
  const handleBackdropClick = useCallback((e: React.MouseEvent) => { if (e.target === e.currentTarget) onClose(); }, [onClose]);
  const handleEscapeKey = useCallback((e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); }, [onClose]);
  const handlePeerSort = useCallback((key: PeerSortKey) => { setPeerSortConfig(prev => ({ key, order: prev.key === key && prev.order === 'asc' ? 'desc' : 'asc' })); }, []);

  useEffect(() => { // Escape key listener
    document.addEventListener('keydown', handleEscapeKey); document.body.style.overflow = 'hidden';
    return () => { document.removeEventListener('keydown', handleEscapeKey); document.body.style.overflow = 'unset'; };
  }, [handleEscapeKey]);

  useEffect(() => { // Fetch History Data
    let isMounted = true; // Prevent state update on unmounted component
    const fetchHistory = async () => {
      if (!isMounted) return;
      setHistoryError(null);
      try {
        const response = await fetch(`/api/host_history/${hostname}`);
        if (!isMounted) return;
        if (!response.ok) { throw new Error(`Failed to fetch history: ${response.status} ${response.statusText}`); }
        const fetchedData: HistoryData = await response.json();
        if (!isMounted) return;
        if (!fetchedData || typeof fetchedData !== 'object' || !fetchedData.timestamps) { throw new Error("Invalid history data format"); }
        setHistoryData(fetchedData);
      } catch (e) {
        if (isMounted) { console.error("Error fetching history:", e); setHistoryError(e instanceof Error ? e.message : "Unknown error"); }
      } finally {
        if (isMounted) { setHistoryLoading(false); }
      }
    };
    setHistoryLoading(true); fetchHistory();
    const interval = setInterval(fetchHistory, 5000);
    return () => { isMounted = false; clearInterval(interval); }; // Cleanup
  }, [hostname]);

  // --- Memoized Calculations ---
  const sortedPeerTraffic = useMemo((): TransformedPeerData[] => { // Peer Traffic Sorting
    if (!data?.peer_traffic) return [];
    const transformed = Object.entries(data.peer_traffic)
      .map(([key, flow]): TransformedPeerData | null => { /* ... map logic ... */
        const parts = key.split('_to_'); if (parts.length !== 2) return null; const [src, dst] = parts;
        const isOutbound = src === data.agent_ip; return { key, direction: isOutbound ? 'outbound' : 'inbound', peerIp: isOutbound ? dst : src, rateMbps: flow?.Mbps ?? 0, };
      })
      .filter((item): item is TransformedPeerData => item !== null);
    transformed.sort((a, b) => { /* ... sort logic ... */
      const key = peerSortConfig.key; const orderMultiplier = peerSortConfig.order === 'asc' ? 1 : -1; let comparison = 0;
      if (key === 'peerIp') comparison = a.peerIp.localeCompare(b.peerIp); else comparison = a.rateMbps - b.rateMbps; return comparison * orderMultiplier;
    });
    return transformed;
  }, [data?.peer_traffic, data?.agent_ip, peerSortConfig]);

  const getSortIcon = (key: PeerSortKey) => { if (peerSortConfig.key !== key) return ArrowUpDown; return peerSortConfig.order === 'asc' ? ArrowUp : ArrowDown; };

  const commonChartOptions: any = useMemo(() => ({ // Chart Options
    responsive: true, maintainAspectRatio: false, scales: { x: { type: 'time' as const, time: { unit: 'second' as const, tooltipFormat: 'HH:mm:ss', displayFormats: { second: 'HH:mm:ss' } }, ticks: { maxTicksLimit: 8, color: '#6b7280', font: { size: 10 } }, grid: { display: false } }, y: { beginAtZero: true, ticks: { color: '#6b7280', font: { size: 10 } }, grid: { color: '#e5e7eb' } } }, plugins: { legend: { display: true, position: 'bottom' as const, labels: { boxWidth: 12, padding: 15, font: { size: 11 } } }, tooltip: { mode: 'index' as const, intersect: false, } }, elements: { point: { radius: 0, hoverRadius: 4 }, line: { tension: 0.1 } }, interaction: { mode: 'index' as const, intersect: false, },
  }), []);

  const cpuChartData = useMemo(() => { // CPU Chart Data
    if (!historyData?.timestamps || !historyData?.cpu_percent) return null; const labels = historyData.timestamps.map(ts => parseISO(ts)); const dataPoints = historyData.cpu_percent; return { labels, datasets: [{ label: 'CPU Usage', data: dataPoints, borderColor: 'rgb(59, 130, 246)', backgroundColor: 'rgba(59, 130, 246, 0.1)', fill: true, yAxisID: 'y', }] };
  }, [historyData]);

  const memChartData = useMemo(() => { // Memory Chart Data
    if (!historyData?.timestamps || !historyData?.mem_percent) return null; const labels = historyData.timestamps.map(ts => parseISO(ts)); const dataPoints = historyData.mem_percent; return { labels, datasets: [{ label: 'Memory Usage', data: dataPoints, borderColor: 'rgb(234, 179, 8)', backgroundColor: 'rgba(234, 179, 8, 0.1)', fill: true, yAxisID: 'y', }] };
  }, [historyData]);

  const networkChartData = useMemo(() => { // Network Chart Data (Prepared for all interfaces)
    if (!historyData?.timestamps || !historyData?.network_interfaces) return {}; const labels = historyData.timestamps.map(ts => parseISO(ts)); const charts: { [iface: string]: any } = {};
    for (const ifaceName in historyData.network_interfaces) { const ifaceHistory = historyData.network_interfaces[ifaceName]; if (ifaceHistory?.sent_Mbps && ifaceHistory?.recv_Mbps) { charts[ifaceName] = { labels, datasets: [{ label: 'Sent', data: ifaceHistory.sent_Mbps, borderColor: 'rgb(239, 68, 68)', backgroundColor: 'rgba(239, 68, 68, 0.1)', fill: false, yAxisID: 'y', }, { label: 'Received', data: ifaceHistory.recv_Mbps, borderColor: 'rgb(34, 197, 94)', backgroundColor: 'rgba(34, 197, 94, 0.1)', fill: false, yAxisID: 'y', }] }; } } return charts;
  }, [historyData]);

  // --- Render ---
  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50 backdrop-blur-sm" onClick={handleBackdropClick}>
      <div className="bg-white rounded-xl shadow-xl max-w-4xl w-full max-h-[90vh] flex flex-col overflow-hidden" onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="p-5 sm:p-6 border-b border-gray-200 flex justify-between items-center flex-shrink-0">
          <div> <h2 className="text-xl sm:text-2xl font-bold text-gray-900">{hostname}</h2> <p className="text-sm sm:text-base text-gray-500">{data?.agent_ip ?? 'N/A'}</p> </div>
          <button onClick={onClose} className="p-2 -mr-2 text-gray-400 hover:bg-gray-100 hover:text-gray-600 rounded-full transition-colors" aria-label="Close modal"> <X className="w-6 h-6" /> </button>
        </div>

        {/* Scrollable Content */}
        <div className="p-5 sm:p-6 overflow-y-auto flex-grow">
          {/* History Loading/Error */}
          {historyLoading && <p className="text-center text-gray-500 py-4 animate-pulse">Loading history...</p>}
          {historyError && <p className="text-center text-red-500 py-4">Error loading history: {historyError}</p>}

          {/* History Charts Section (if data loaded) */}
          {historyData && !historyLoading && !historyError && (
            <>
              {/* Performance History */}
              <section className="mb-8">
                <h3 className="text-lg font-semibold mb-4 text-gray-800">Performance History</h3>
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                  {/* CPU Chart */}
                  <div className="bg-gray-50 p-3 rounded-lg shadow-sm border border-gray-100">
                    <h4 className="text-sm font-medium text-center text-gray-600 mb-2">CPU Usage (%)</h4>
                    <div className="h-40"> {cpuChartData ? (<Line options={{ ...commonChartOptions, plugins: { ...commonChartOptions.plugins, legend: { display: false } } }} data={cpuChartData} />) : (<p className="h-full flex items-center justify-center text-gray-400 text-sm">No CPU data</p>)} </div>
                  </div>
                  {/* Memory Chart */}
                  <div className="bg-gray-50 p-3 rounded-lg shadow-sm border border-gray-100">
                    <h4 className="text-sm font-medium text-center text-gray-600 mb-2">Memory Usage (%)</h4>
                    <div className="h-40"> {memChartData ? (<Line options={{ ...commonChartOptions, plugins: { ...commonChartOptions.plugins, legend: { display: false } } }} data={memChartData} />) : (<p className="h-full flex items-center justify-center text-gray-400 text-sm">No Memory data</p>)} </div>
                  </div>
                </div>
              </section>

              {/* Network History (Filtered) */}
              <section className="mb-6">
                <h3 className="text-lg font-semibold mb-4 text-gray-800">Network History (Mbps)</h3>
                {(() => {
                  const interfacesWithTraffic = Object.entries(networkChartData).filter(([_ifaceName, chartData]) => { /* ... filter logic ... */ const sentData = chartData?.datasets?.[0]?.data || []; const recvData = chartData?.datasets?.[1]?.data || []; const threshold = 0.01; const hasSentTraffic = sentData.some((val: number | null) => val !== null && val > threshold); const hasRecvTraffic = recvData.some((val: number | null) => val !== null && val > threshold); return hasSentTraffic || hasRecvTraffic; });
                  if (interfacesWithTraffic.length === 0) { return Object.keys(networkChartData).length === 0 ? (<p className="text-center text-gray-400 text-sm">No Network history data found.</p>) : (<p className="text-center text-gray-400 text-sm">No significant network traffic activity found.</p>); }
                  return (<div className="space-y-6"> {interfacesWithTraffic.map(([ifaceName, chartData]) => (<div key={ifaceName} className="bg-gray-50 p-3 rounded-lg shadow-sm border border-gray-100"> <h4 className="text-sm font-medium text-center text-gray-600 mb-2">{ifaceName}</h4> <div className="h-48"><Line options={commonChartOptions} data={chartData} /></div> </div>))} </div>);
                })()}
              </section>
            </>
          )}

          {/* Latest Data Section (if data prop exists) */}
          {data ? (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mt-6 border-t pt-6">
              {/* Left Column: Latest Resources & Active Network */}
              <div className="space-y-6">
                {/* Latest Resources */}
                <section aria-labelledby={`sys-resources-latest-${hostname}`}> <h3 id={`sys-resources-latest-${hostname}`} className="text-lg font-semibold mb-3 text-gray-800">Latest Resources</h3> <div className="space-y-4"> <div> <div className="flex justify-between items-baseline mb-1"> <span className="text-sm font-medium text-gray-600">CPU Usage</span> <span className="text-sm font-semibold text-gray-800">{data.cpu_percent >= 0 ? `${data.cpu_percent.toFixed(1)}%` : 'N/A'}</span> </div> <ProgressBar value={data.cpu_percent} large /> </div> <div> <div className="flex justify-between items-baseline mb-1"> <span className="text-sm font-medium text-gray-600">Memory Usage</span> <span className="text-sm font-semibold text-gray-800">{data.mem_percent >= 0 ? `${data.mem_percent.toFixed(1)}%` : 'N/A'}</span> </div> <ProgressBar value={data.mem_percent} large /> </div> </div> </section>
                {/* Active Network Interfaces Table */}
                <section aria-labelledby={`net-interfaces-latest-${hostname}`}> <h3 id={`net-interfaces-latest-${hostname}`} className="text-lg font-semibold mb-3 text-gray-800">Active Network Interfaces</h3> {data.network_adapters && Object.keys(data.network_adapters).length > 0 ? ((() => { const activeAdapters = Object.entries(data.network_adapters).filter(([, adapter]: [string, NetworkAdapter | null]) => adapter?.is_up === true); if (activeAdapters.length === 0) { return <p className="text-sm text-gray-500 italic">No active interfaces.</p>; } return (<div className="overflow-x-auto border border-gray-200 rounded-lg"> <table className="w-full text-sm"> <thead className="bg-gray-50"><tr className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wider"><th className="px-4 py-2">Interface</th><th className="px-4 py-2">Utilization</th><th className="px-4 py-2 text-right">Speed (Mbps)</th></tr></thead> <tbody className="bg-white divide-y divide-gray-200"> {activeAdapters.map(([name, adapter]: [string, NetworkAdapter]) => { const isChoked = adapter.is_choked ?? false; const utilization = adapter.utilization_percent ?? -1; const utilizationDisplay = utilization >= 0 ? `${utilization.toFixed(1)}%` : 'N/A'; const linkSpeedDisplay = adapter.link_speed_mbps > 0 ? adapter.link_speed_mbps : 'N/A'; const rowClass = isChoked ? 'bg-yellow-50 hover:bg-yellow-100' : 'hover:bg-gray-50'; return (<tr key={name} className={`${rowClass} transition-colors duration-150 ease-in-out`}> <td className="px-4 py-2 font-medium text-gray-900 whitespace-nowrap"> {isChoked && (<span title={`High Utilization (${utilization.toFixed(1)}%)`}><AlertTriangle className="w-4 h-4 text-yellow-500 inline-block mr-1 -mt-px" /></span>)} {name} </td> <td className="px-4 py-2"><div className="flex items-center gap-2"> <div className={`relative w-full ${isChoked ? 'ring-1 ring-yellow-400 ring-offset-1 rounded-full p-px' : ''}`}><ProgressBar value={Math.max(0, utilization)} small /></div> <span className={`text-xs font-medium whitespace-nowrap ${isChoked ? 'text-yellow-700 font-bold' : 'text-gray-700'}`}>{utilizationDisplay}</span> </div></td> <td className={`px-4 py-2 text-right ${linkSpeedDisplay === 'N/A' ? 'text-gray-400' : 'text-gray-700'}`}>{linkSpeedDisplay}</td> </tr>); })} </tbody> </table> </div>); })()) : (<p className="text-sm text-gray-500 italic">No latest interface data.</p>)} </section>
              </div>
              {/* Right Column: Latest Disk & Peer Traffic */}
              <div className="space-y-6">
                {/* Latest Disk Usage */}
                <section aria-labelledby={`disk-usage-latest-${hostname}`}> <h3 id={`disk-usage-latest-${hostname}`} className="text-lg font-semibold mb-3 text-gray-800">Disk Usage</h3> {data.disks && Object.keys(data.disks).length > 0 ? (<div className="space-y-4"> {Object.entries(data.disks).map(([drive, info]: [string, DiskInfo]) => (<div key={drive}> <div className="flex justify-between items-baseline mb-1"><span className="text-sm font-medium text-gray-600">{drive}</span><span className="text-xs text-gray-500">{info.free_gb?.toFixed(1) ?? 'N/A'} GB free / {info.total_gb?.toFixed(1) ?? 'N/A'} GB total</span></div> <ProgressBar value={info.percent >= 0 ? info.percent : 0} /> </div>))} </div>) : (<p className="text-sm text-gray-500 italic">No disk usage data.</p>)} </section>
                {/* *** ADDED SECTION: Latest Disk I/O *** */}
                <section aria-labelledby={`disk-io-latest-${hostname}`}>
                  <h3 id={`disk-io-latest-${hostname}`} className="text-lg font-semibold mb-3 text-gray-800 dark:text-gray-200 flex items-center gap-2"> {/* Added dark mode & Icon */}
                    <HardDrive className="w-5 h-5 text-gray-500" />
                    Disk I/O
                  </h3>
                  {(data.disk_io && Object.keys(data.disk_io).length > 0) ? (
                    <div className="overflow-x-auto border border-gray-200 dark:border-gray-600 rounded-lg"> {/* Added dark mode */}
                      <table className="w-full text-sm">
                        <thead className="bg-gray-50 dark:bg-gray-700"> {/* Added dark mode */}
                          <tr className="text-left text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider"> {/* Added dark mode */}
                            <th className="px-4 py-2">Disk</th>
                            <th className="px-4 py-2 text-right">Read IOPS</th>
                            <th className="px-4 py-2 text-right">Write IOPS</th>
                            <th className="px-4 py-2 text-right">Read</th>
                            <th className="px-4 py-2 text-right">Write</th>
                          </tr>
                        </thead>
                        <tbody className="bg-white dark:bg-gray-800 divide-y divide-gray-200 dark:divide-gray-600"> {/* Added dark mode */}
                          {Object.entries(data.disk_io).map(([diskName, ioStats]: [string, DiskIoStats]) => (
                            <tr key={diskName} className="hover:bg-gray-50 dark:hover:bg-gray-700/50"> {/* Added dark mode */}
                              <td className="px-4 py-2 font-medium text-gray-900 dark:text-gray-100 whitespace-nowrap">{diskName}</td> {/* Added dark mode */}
                              <td className="px-4 py-2 text-right text-gray-700 dark:text-gray-300">{ioStats.read_ops_ps?.toFixed(1) ?? 'N/A'}</td> {/* Added dark mode */}
                              <td className="px-4 py-2 text-right text-gray-700 dark:text-gray-300">{ioStats.write_ops_ps?.toFixed(1) ?? 'N/A'}</td> {/* Added dark mode */}
                              <td className="px-4 py-2 text-right text-gray-700 dark:text-gray-300">{formatBytesPerSecond(ioStats.read_Bps)}</td> {/* Use formatter, Added dark mode */}
                              <td className="px-4 py-2 text-right text-gray-700 dark:text-gray-300">{formatBytesPerSecond(ioStats.write_Bps)}</td> {/* Use formatter, Added dark mode */}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <p className="text-sm text-gray-500 dark:text-gray-400 italic">No Disk I/O data available.</p> /* Added dark mode */
                  )}
                </section>
                {/* *** END ADDED SECTION *** */}
                {/* Latest Peer Traffic */}
                <section aria-labelledby={`peer-traffic-latest-${hostname}`}> <h3 id={`peer-traffic-latest-${hostname}`} className="text-lg font-semibold mb-3 text-gray-800">Latest Peer Traffic</h3> {sortedPeerTraffic.length > 0 ? (<div className="overflow-x-auto border border-gray-200 rounded-lg"> <table className="w-full text-sm"> <thead className="bg-gray-50"><tr className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wider"><th className="px-4 py-2">Dir</th><th className="px-4 py-2"><button onClick={() => handlePeerSort('peerIp')} className="flex items-center gap-1 hover:text-gray-700" aria-label={`Sort Peer`}>Peer <span className={peerSortConfig.key === 'peerIp' ? 'text-gray-700' : 'text-gray-400'}>{React.createElement(getSortIcon('peerIp'), { className: "w-3 h-3" })}</span></button></th><th className="px-4 py-2 text-right"><button onClick={() => handlePeerSort('rateMbps')} className="flex items-center gap-1 float-right hover:text-gray-700" aria-label={`Sort Rate`}>Rate (Mbps) <span className={peerSortConfig.key === 'rateMbps' ? 'text-gray-700' : 'text-gray-400'}>{React.createElement(getSortIcon('rateMbps'), { className: "w-3 h-3" })}</span></button></th></tr></thead> <tbody className="bg-white divide-y divide-gray-200">{sortedPeerTraffic.map((flow) => (<tr key={flow.key}><td className="px-4 py-2">{flow.direction === 'outbound' ? (<span title="Outbound" className="text-red-500">→</span>) : (<span title="Inbound" className="text-green-500">←</span>)}</td><td className="px-4 py-2 font-medium text-gray-900">{flow.peerIp}</td><td className="px-4 py-2 text-right text-gray-700">{flow.rateMbps.toFixed(2)}</td></tr>))}</tbody> </table> </div>) : (<p className="text-sm text-gray-500 italic">No latest peer traffic.</p>)} </section>
              </div>
            </div>
          ) : (
            <p className="text-center text-gray-500 mt-6">Latest host data not available.</p>
          )}
        </div> {/* End Scrollable Content */}
      </div> {/* End Modal Container */}
    </div> // End Backdrop
  );
}

export default HostDetailModal;