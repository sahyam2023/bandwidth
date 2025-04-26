import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { Loader2, AlertTriangle, CheckCircle, WifiOff, Link as LinkIcon, ArrowRightLeft, Users, MinusCircle, ArrowUpRight, Signal } from 'lucide-react';
import { Tooltip as ReactTooltip } from 'react-tooltip';
import 'react-tooltip/dist/react-tooltip.css';

// --- Types (Define or import from shared types) ---
interface ConnectivityNode {
    id: string; // Hostname or Collector ID
    name: string;
    is_collector: boolean;
    ip?: string;
}
interface ConnectivityLink {
    source: string; // Source Node ID
    target: string; // Target Node ID
    status: 'success' | 'timeout' | 'error' | 'unknown';
    latency_ms: number | null;
}
interface ConnectivityData {
    nodes: ConnectivityNode[];
    links: ConnectivityLink[];
}

interface PeerFlowLink {
    source: string; // Source IP
    target: string; // Target IP
    rate_mbps: number;
}
interface PeerFlowData {
    nodes: { id: string, name: string, hostname?: string }[]; // Node ID is IP
    links: PeerFlowLink[];
}

// --- Helper to get node name from ID (Hostname) ---
const getNodeName = (nodeId: string, nodes: ConnectivityNode[]): string => {
    const node = nodes.find(n => n.id === nodeId);
    return node?.name || nodeId; // Fallback to ID if name not found
};

// --- Helper to get node name from IP (for peer flows) ---
const getNodeNameFromIp = (ip: string, nodes: PeerFlowData['nodes']): string => {
    const node = nodes.find(n => n.id === ip);
    return node?.name || ip; // Fallback to IP if name not found
};

function NetworkFocusPage() {
    const [connectivityData, setConnectivityData] = useState<ConnectivityData | null>(null);
    const [peerFlowData, setPeerFlowData] = useState<PeerFlowData | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

    const fetchData = useCallback(async () => {
        // setLoading(true); // Set loading only on initial load maybe
        setError(null);
        try {
            const [connResponse, flowResponse] = await Promise.all([
                fetch('/api/connectivity_status'),
                fetch('/api/all_peer_flows') // Fetch peer flows for top talkers
            ]);

            if (!connResponse.ok) throw new Error(`Connectivity API Error: ${connResponse.status}`);
            if (!flowResponse.ok) throw new Error(`Peer Flow API Error: ${flowResponse.status}`);

            const connData: ConnectivityData = await connResponse.json();
            const flowData: PeerFlowData = await flowResponse.json();

            // TODO: Add validation for received data structures
            setConnectivityData(connData);
            setPeerFlowData(flowData);
            setLastUpdated(new Date());

        } catch (e) {
            console.error("Error fetching network focus data:", e);
            setError(e instanceof Error ? e.message : "Unknown error fetching data");
            // Optionally clear old data on error?
            // setConnectivityData(null);
            // setPeerFlowData(null);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        setLoading(true);
        fetchData();
        const interval = setInterval(fetchData, 10000); // Refresh every 10s
        return () => clearInterval(interval);
    }, [fetchData]);
    
    // --- Calculate Top Talkers ---
    const topTalkers = useMemo(() => {
        if (!peerFlowData || !peerFlowData.links) return [];
        // Sort links by rate descending
        return [...peerFlowData.links]
            .sort((a, b) => (b.rate_mbps || 0) - (a.rate_mbps || 0))
            .slice(0, 15); // Show top 15 flows
    }, [peerFlowData]);

    return (
        <div className="p-4 sm:p-6 h-full flex flex-col">
            <h1 className="text-2xl sm:text-3xl font-bold text-gray-900 dark:text-gray-100 mb-1">
                Network Focus
            </h1>
            {lastUpdated && <p className="text-xs text-gray-500 dark:text-gray-400 mb-4">Last Updated: {lastUpdated.toLocaleTimeString()}</p>}

            {/* Loading / Error States */}
            {loading && (
                <div className="flex-grow flex items-center justify-center text-gray-500 dark:text-gray-400">
                     <Loader2 size={24} className="animate-spin mr-2"/> Loading network data...
                </div>
            )}
            {error && !loading && (
                 <div className="flex-grow flex items-center justify-center p-4 text-red-600 dark:text-red-400">
                      <AlertTriangle size={24} className="mr-2"/> Error: {error}
                 </div>
            )}

            {/* Content Area */}
            {!loading && !error && (
                <div className="flex-grow grid grid-cols-1 lg:grid-cols-2 gap-6 items-start">

                     {/* === Column 1: Connectivity Status Table === */}
                    <div className="p-4 border rounded-lg bg-white dark:bg-gray-800 shadow-sm flex flex-col">
                         <h2 className="text-lg font-semibold mb-3 text-gray-800 dark:text-gray-200 flex items-center gap-2 flex-shrink-0">
                             <LinkIcon size={18} /> Node Connectivity (Ping Status)
                         </h2>
                         {connectivityData && connectivityData.links.length > 0 ? (
                             // Added overflow-auto for table scroll
                             <div className="overflow-auto flex-grow">
                                 <table className="min-w-full text-sm">
                                     <thead className="sticky top-0 bg-gray-50 dark:bg-gray-700 z-10">
                                         <tr>
                                             <th className="py-2 px-2 text-left font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">From</th>
                                             <th className="py-2 px-1 text-center font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider"></th> {/* For Arrow */}
                                             <th className="py-2 px-2 text-left font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">To</th>
                                             <th className="py-2 px-2 text-center font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Status</th>
                                             <th className="py-2 px-2 text-right font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Latency</th>
                                         </tr>
                                     </thead>
                                     <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
                                         {connectivityData.links.map((link, index) => {
                                            const sourceName = getNodeName(link.source, connectivityData.nodes);
                                            const targetName = getNodeName(link.target, connectivityData.nodes);
                                            const latencyText = link.latency_ms !== null ? `${link.latency_ms.toFixed(1)} ms` : '-';
                                            let statusIcon;
                                            let statusColor = 'text-gray-500 dark:text-gray-400';
                                            let statusTooltip = link.status;

                                            switch (link.status) {
                                                case 'success':
                                                    statusIcon = <CheckCircle size={14} />;
                                                    statusColor = 'text-green-500';
                                                    break;
                                                case 'timeout':
                                                    statusIcon = <WifiOff size={14} />;
                                                    statusColor = 'text-yellow-500';
                                                    break;
                                                case 'error':
                                                    statusIcon = <AlertTriangle size={14} />;
                                                    statusColor = 'text-red-500';
                                                    break;
                                                default:
                                                    statusIcon = <MinusCircle size={14} />;
                                                    statusColor = 'text-gray-400';
                                                    statusTooltip = 'unknown';
                                                    break;
                                            }

                                             return (
                                                <tr key={`${link.source}-${link.target}-${index}`} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                                                    <td className="px-2 py-1.5 font-medium text-gray-800 dark:text-gray-200 truncate" title={sourceName}>{sourceName}</td>
                                                    <td className="px-1 py-1.5 text-center text-gray-400">→</td>
                                                    <td className="px-2 py-1.5 font-medium text-gray-800 dark:text-gray-200 truncate" title={targetName}>{targetName}</td>
                                                     <td className="px-2 py-1.5 text-center">
                                                         <span className={`${statusColor} inline-block`} data-tooltip-id="conn-tooltip" data-tooltip-content={statusTooltip}>
                                                             {statusIcon}
                                                         </span>
                                                     </td>
                                                     <td className={`px-2 py-1.5 text-right font-mono text-xs ${ link.latency_ms !== null && link.latency_ms >= 100 ? 'text-orange-500' : 'text-gray-500 dark:text-gray-400' }`}>
                                                         {latencyText}
                                                     </td>
                                                 </tr>
                                             );
                                         })}
                                     </tbody>
                                 </table>
                             </div>
                         ) : (
                              <p className="text-sm text-gray-400 italic flex-grow flex items-center justify-center">No connectivity links found.</p>
                          )}
                    </div>

                    {/* === Column 2: Top Peer Traffic Flows Table === */}
                    <div className="p-4 border rounded-lg bg-white dark:bg-gray-800 shadow-sm flex flex-col">
                        <h2 className="text-lg font-semibold mb-3 text-gray-800 dark:text-gray-200 flex items-center gap-2 flex-shrink-0">
                            <ArrowRightLeft size={18} /> Top Peer Traffic Flows
                         </h2>
                         {topTalkers && topTalkers.length > 0 ? (
                             <div className="overflow-auto flex-grow">
                                 <table className="min-w-full text-sm">
                                     <thead className="sticky top-0 bg-gray-50 dark:bg-gray-700 z-10">
                                         <tr>
                                             <th className="py-2 px-2 text-left font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Source</th>
                                             <th className="py-2 px-1 text-center font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider"></th> {/* Arrow */}
                                             <th className="py-2 px-2 text-left font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Target</th>
                                             <th className="py-2 px-2 text-right font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">Rate (Mbps)</th>
                                         </tr>
                                     </thead>
                                     <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
                                         {topTalkers.map((link, index) => {
                                             // Use helper to get names from IPs (peerFlowData.nodes maps IP to name/hostname)
                                             const sourceName = getNodeNameFromIp(link.source, peerFlowData?.nodes || []);
                                             const targetName = getNodeNameFromIp(link.target, peerFlowData?.nodes || []);
                                             return (
                                                  <tr key={`${link.source}-${link.target}-${index}`} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                                                     <td className="px-2 py-1.5 font-medium text-gray-800 dark:text-gray-200 truncate" title={sourceName}>{sourceName}</td>
                                                      <td className="px-1 py-1.5 text-center text-blue-500">
                                                           <ArrowUpRight size={14} />
                                                      </td>
                                                      <td className="px-2 py-1.5 font-medium text-gray-800 dark:text-gray-200 truncate" title={targetName}>{targetName}</td>
                                                      <td className="px-2 py-1.5 text-right font-mono text-purple-600 dark:text-purple-400">
                                                          {(link.rate_mbps || 0).toFixed(2)}
                                                      </td>
                                                  </tr>
                                             );
                                         })}
                                     </tbody>
                                 </table>
                             </div>
                         ) : (
                              <p className="text-sm text-gray-400 italic flex-grow flex items-center justify-center">No peer traffic data found.</p>
                          )}
                    </div>
                </div>
            )}
            {/* Tooltip Component - fixed properties */}
            <ReactTooltip id="conn-tooltip" place="top" />
        </div>
    );
}

export default NetworkFocusPage;