// src/App.tsx
import { useState, useEffect, useCallback, useMemo, } from 'react'; // Keep React imports
// --- React Router Imports ---
import { Routes, Route, Link, useLocation } from 'react-router-dom';
// --- Icon Imports ---
import { Activity, LayoutDashboard, Network, Users, AlertCircle, BarChart2, GitBranch, Filter, History as HistoryIcon, Bell, Share2 } from 'lucide-react';
// --- Component Imports ---
import HostCard from './components/HostCard';
import HostDetailModal from './components/HostDetailModal';
import PeerTrafficPage from './pages/PeerTrafficPage';
import NetworkFocusPage from './pages/NetworkFocusPage';
// --- Util/Type Imports ---
import { sortHosts } from './utils/sorting';
import { HostData, SortKey, SortOrder, AlertInfo, SystemData, SelectOption } from './types'; import AlertPanel from './components/AlertPanel';
import AlertsPage from './pages/AlertsPage';
import Select from 'react-select';

import { ToastContainer, toast } from 'react-toastify';
import 'react-toastify/dist/ReactToastify.css'; // Import default CSS
import HistoryPage from './pages/HistoryPage';

// --- Interfaces ---
interface SummaryData {
    total_agents_active: number;
    agents_with_alerts: number;
    total_network_throughput_mbps: number;
    total_peer_traffic_mbps: number;
}

// --- Constants ---
const API_ENDPOINT = '/api/latest_data';
const FETCH_INTERVAL_MS = 2000; // Use the 2s interval

const filterableNumericMetrics: SelectOption[] = [
    { value: 'cpu_percent', label: 'CPU Usage (%)' },
    { value: 'mem_percent', label: 'Memory Usage (%)' },
    { value: 'total_throughput_mbps', label: 'Network Total (Mbps)' },
    // Add disk usage % or IOPS later if needed, requires parsing JSON
];

interface DashboardProps {
    addAlert: (alertData: Omit<AlertInfo, 'timestamp'>) => void;
}

// ========================================================================
//          Dashboard Component (Self-contained Logic)
// ========================================================================
function Dashboard({ addAlert }: DashboardProps) {
    // State specific to the Dashboard view
    const [hosts, setHosts] = useState<Record<string, HostData>>({});
    // CHANGE 1: Use state for previous hosts instead of ref
    const [prevHosts, setPrevHosts] = useState<Record<string, HostData>>({});
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [selectedHost, setSelectedHost] = useState<string | null>(null);
    const [sortConfig, setSortConfig] = useState<{ key: SortKey; order: SortOrder }>({ key: 'hostname', order: 'asc' });
    const [searchTerm, setSearchTerm] = useState('');

    const [filterMetric, setFilterMetric] = useState<SelectOption | null>(null); // Which metric to filter on
    const [filterOperator, setFilterOperator] = useState<'>=' | '<='>('>='); // Operator
    const [filterValue, setFilterValue] = useState<string>(''); // Value as string for input

    // --- NEW: State for Summary Data ---
    const [summaryData, setSummaryData] = useState<SummaryData | null>(null);
    const [summaryLoading, setSummaryLoading] = useState(true);
    const [summaryError, setSummaryError] = useState<string | null>(null);
    const [filterByAlert, setFilterByAlert] = useState(false);

    // --- MODIFIED: Fetch data logic ---
    const fetchData = useCallback(async () => {
        // Reset errors before fetching
        setError(null);
        setSummaryError(null);

        // Use Promise.allSettled to fetch both endpoints concurrently
        // and handle errors independently
        const results = await Promise.allSettled([
            fetch(API_ENDPOINT), // Fetch latest host data
            fetch('/api/summary') // Fetch summary data
        ]);

        let hostDataSuccess = false;
        let summaryDataSuccess = false;

        // Process host data result
        const hostResult = results[0];
        if (hostResult.status === 'fulfilled') {
            try {
                const response = hostResult.value;
                if (!response.ok) { throw new Error(`Collector Error: ${response.status} ${response.statusText || ''}`.trim()); }
                const data = await response.json();
                if (typeof data !== 'object' || data === null) { throw new Error("Invalid host data format received."); }
                setHosts(data);
                hostDataSuccess = true;
            } catch (e) {
                console.error("Dashboard host fetch error:", e);
                setError(e instanceof Error ? e.message : 'An unknown error occurred while fetching host data.');
            }
        } else { // Rejected promise
            console.error("Dashboard host fetch rejected:", hostResult.reason);
            setError(hostResult.reason instanceof Error ? hostResult.reason.message : 'Failed to fetch host data.');
        }

        // Process summary data result
        const summaryResult = results[1];
        if (summaryResult.status === 'fulfilled') {
            try {
                const response = summaryResult.value;
                if (!response.ok) { throw new Error(`Summary Error: ${response.status} ${response.statusText || ''}`.trim()); }
                const data: SummaryData = await response.json();
                // Optional: Add basic validation for summary data structure
                if (typeof data?.total_agents_active !== 'number') { throw new Error("Invalid summary data format."); }
                setSummaryData(data);
                summaryDataSuccess = true;
            } catch (e) {
                console.error("Dashboard summary fetch error:", e);
                setSummaryError(e instanceof Error ? e.message : 'An unknown error occurred while fetching summary data.');
            }
        } else { // Rejected promise
            console.error("Dashboard summary fetch rejected:", summaryResult.reason);
            setSummaryError(summaryResult.reason instanceof Error ? summaryResult.reason.message : 'Failed to fetch summary data.');
        }

        // Only set global loading to false if *initial* host data load succeeded or failed
        // Subsequent errors shouldn't block the whole view if we have older data
        if (!loading || !hostDataSuccess) { // Only update loading state after initial load attempt
            setLoading(false);
        }
        setSummaryLoading(false); // Summary loading is independent
    }, [loading]); // Add loading to dependency array

    useEffect(() => {
        // setLoading(true); // Set loading true when component mounts
        // setSummaryLoading(true); // Also set summary loading true
        fetchData(); // Initial fetch
        const interval = setInterval(fetchData, FETCH_INTERVAL_MS); // Set up polling
        return () => clearInterval(interval); // Cleanup on unmount
    }, [fetchData]);


    // CHANGE 3 & 4 & 5: Combined effect for Choke Detection and prevHosts update
    useEffect(() => {
        // CHANGE 4: Run comparison first, comparing current hosts with prevHosts
        if (!loading && Object.keys(prevHosts).length > 0 && Object.keys(hosts).length > 0) {
            for (const hostname in hosts) {
                const currentHostData = hosts[hostname]?.data;
                const prevHostData = prevHosts[hostname]?.data;

                if (currentHostData?.network_adapters && prevHostData?.network_adapters) {
                    for (const ifaceName in currentHostData.network_adapters) {
                        const currentAdapter = currentHostData.network_adapters[ifaceName];
                        const prevAdapterWasChoked = prevHostData.network_adapters?.[ifaceName]?.is_choked === true;

                        const justBecameChoked = currentAdapter?.is_choked === true && !prevAdapterWasChoked;
                        if (justBecameChoked) {
                            const alertId = `choke-${hostname}-${ifaceName}`;
                            const message = `High network utilization on ${hostname} (${ifaceName}) - ${currentAdapter.utilization_percent?.toFixed(1)}%`;

                            // --- NEW: Call addAlert prop ---
                            addAlert({
                                id: alertId,
                                type: 'warning',
                                message: message,
                                hostname: hostname,
                                interfaceName: ifaceName
                            });

                            // Still show toast as well
                            toast.warn(
                                message,
                                {
                                    toastId: alertId,
                                    autoClose: 10000
                                }
                            );
                        }
                    }
                }
            }
        }

        // CHANGE 5: Update prevHosts AFTER comparison
        // Only update if hosts reference changed to prevent infinite loops
        if (hosts !== prevHosts) {
            setPrevHosts(hosts);
        }
        // CHANGE 3: Add prevHosts and addAlert to dependency array
    }, [hosts, prevHosts, loading, addAlert]);

    // Sorting handler specific to dashboard hosts
    const handleSort = useCallback((key: SortKey) => {
        setSortConfig(prev => ({ key, order: prev.key === key && prev.order === 'asc' ? 'desc' : 'asc' }));
    }, []);

    // Filtering and Sorting specific to dashboard hosts
    const filteredAndSortedHosts = useMemo(() => {
        const numericFilterValue = filterValue.trim() === '' ? null : parseFloat(filterValue);
        return Object.entries(hosts)
            .filter(([hostname, hostData]: [string, HostData]) => {
                if (!hostData?.data?.agent_ip) return false; // Basic check

                // Search Term Filter
                const searchLower = searchTerm.toLowerCase();
                const matchesSearch = hostname.toLowerCase().includes(searchLower) ||
                    hostData.data.agent_ip.toLowerCase().includes(searchLower);

                if (!matchesSearch) return false; // Exit early if search doesn't match

                // Alert Filter
                if (filterByAlert) {
                    // Check if the has_alert flag is true in the data
                    const hasAlert = hostData.data.has_alert === true;
                    if (!hasAlert) return false; // Exclude if filtering by alert and host has no alert
                }
                if (filterMetric && numericFilterValue !== null && !isNaN(numericFilterValue)) {
                    const metricKey = filterMetric.value as keyof SystemData; // Type assertion
                    let hostValue = hostData.data[metricKey];

                    // Handle potential non-numeric values fetched (e.g., N/A represented as -1 or null)
                    if (typeof hostValue !== 'number' || hostValue < 0) {
                        // Decide how to handle hosts with N/A data for the selected metric
                        // Option 1: Exclude them from the filter results
                        return false;
                        // Option 2: Include them only if the filter is '<=' and value is high? (complex)
                    }

                    // Apply the comparison based on the selected operator
                    if (filterOperator === '>=') {
                        if (!(hostValue >= numericFilterValue)) {
                            return false; // Exclude if condition not met
                        }
                    } else if (filterOperator === '<=') {
                        if (!(hostValue <= numericFilterValue)) {
                            return false; // Exclude if condition not met
                        }
                    }
                }

                return true; // Include if all filters pass
            })
            .sort(([, a]: [string, HostData], [, b]: [string, HostData]) => sortHosts(a, b, sortConfig.key, sortConfig.order));
    }, [hosts, searchTerm, sortConfig, filterByAlert, filterMetric, filterOperator, filterValue]);

    // Modal control specific to dashboard hosts
    const handleSelectHost = useCallback((hostname: string) => { setSelectedHost(hostname); }, []);
    const handleCloseModal = useCallback(() => { setSelectedHost(null); }, []);

    // --- Loading / Error States for Dashboard ---
    // Display loading spinner only on initial load
    if (loading && Object.keys(hosts).length === 0) {
        return <div className="flex items-center justify-center p-10"><Activity className="w-12 h-12 animate-spin text-blue-500" /></div>;
    }

    // Show error page only if loading is finished and there are NO hosts AND there is an error
    if (!loading && error && Object.keys(hosts).length === 0) {
        return (
            <div className="flex items-center justify-center p-10">
                <div className="bg-red-50 border border-red-200 rounded-lg p-6 max-w-2xl mx-4 text-center">
                    <h2 className="text-red-700 text-lg font-semibold mb-2">Error Loading Dashboard</h2>
                    <p className="text-red-600">{error}</p>
                    <p className="text-red-500 mt-4 text-sm"> Please check if the collector service is running and accessible at {API_ENDPOINT}. </p>
                    <button onClick={fetchData} className="mt-4 px-4 py-2 bg-red-600 text-white rounded hover:bg-red-700"> Retry </button>
                </div>
            </div>
        );
    }


    // --- Dashboard Render Logic ---
    return (
        // Use padding within the content area provided by the main App layout
        <div className="p-4 sm:p-6">
            {/* Header Section */}
            {/* --- MODIFIED: Wrap controls for better layout --- */}
            <div className="mb-4 flex flex-col md:flex-row md:items-end gap-4 border-b dark:border-gray-700 pb-4">
                {/* Left Side: Title */}
                <div className="flex-shrink-0">
                    <h1 className="text-2xl sm:text-3xl font-bold text-gray-900 dark:text-gray-100"> Dashboard </h1>
                </div>

                {/* Right Side: Filters & Search */}
                <div className="flex-grow flex flex-col sm:flex-row sm:items-end sm:justify-end gap-4">
                    {/* Value Filter Controls */}
                    <div className="flex items-end gap-2 flex-wrap">
                        {/* Metric Select */}
                        <div className="min-w-[180px]">
                            <label htmlFor="filter-metric-select" className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1"> Filter by Metric </label>
                            <Select<SelectOption, false> // Single select
                                instanceId="filter-metric-select"
                                options={filterableNumericMetrics}
                                value={filterMetric}
                                onChange={(selectedOption) => setFilterMetric(selectedOption)}
                                placeholder="Metric..."
                                isClearable={true} // Allow clearing selection
                                className="react-select-container text-sm"
                                classNamePrefix="react-select"
                                styles={{ /* Reuse or define styles */ }}
                            />
                        </div>
                        {/* Operator Select */}
                        <div className="flex-shrink-0">
                            {/* Label hidden visually but present for screen readers */}
                            <label htmlFor="filter-operator-select" className="sr-only"> Operator </label>
                            <select
                                id="filter-operator-select"
                                value={filterOperator}
                                onChange={(e) => setFilterOperator(e.target.value as '>=' | '<=')}
                                disabled={!filterMetric} // Disable if no metric is selected
                                className="h-[38px] px-2 border border-gray-300 dark:border-gray-600 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 disabled:opacity-50"
                            >
                                <option value=">=">≥</option>
                                <option value="<=">≤</option>
                            </select>
                        </div>
                        {/* Value Input */}
                        <div className="flex-shrink-0 w-20">
                            {/* Label hidden visually */}
                            <label htmlFor="filter-value-input" className="sr-only"> Value </label>
                            <input
                                type="number"
                                id="filter-value-input"
                                value={filterValue}
                                onChange={(e) => setFilterValue(e.target.value)}
                                disabled={!filterMetric} // Disable if no metric is selected
                                placeholder="Value"
                                className="h-[38px] w-full px-2 py-1 border border-gray-300 dark:border-gray-600 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 disabled:opacity-50"
                            />
                        </div>
                    </div>

                    {/* Alert Filter Checkbox */}
                    <div className="flex items-end pb-1"> {/* Align with bottom of inputs */}
                        <label className="flex items-center gap-2 cursor-pointer text-sm text-gray-600 dark:text-gray-300 whitespace-nowrap hover:text-gray-800 dark:hover:text-gray-100">
                            {/* ... Alert filter checkbox code (keep existing) ... */}
                            <Filter className="w-4 h-4 text-gray-500" />
                            <input type="checkbox" /*...*/ checked={filterByAlert} onChange={(e) => setFilterByAlert(e.target.checked)} />
                            <span>Show alerts only</span>
                        </label>
                    </div>

                    {/* Search Input */}
                    <div className="flex items-end">
                        <input
                            type="text"
                            placeholder="Search hostname or IP..." // <-- Use the more descriptive placeholder
                            // v-- Use the styles from the second input, ADDING h-[38px] for alignment --v
                            className="w-full sm:w-64 h-[38px] px-4 py-2 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                            value={searchTerm}
                            onChange={(e) => setSearchTerm(e.target.value)}
                        />
                    </div>
                </div>
            </div>

            {/* NEW: System Monitor Dashboard Section */}
            <div className="mb-6">
                <div className="flex flex-col sm:flex-row justify-between items-center mb-4 gap-4">
                    <h1 className="text-2xl sm:text-3xl font-bold text-gray-900 dark:text-gray-100 text-center sm:text-left">
                        System Monitor Dashboard
                    </h1>
                </div>
            </div>

            {/* --- NEW: Summary Stats Section --- */}
            <div className="mb-6 sm:mb-8">
                {summaryLoading && !summaryData ? (
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4 animate-pulse">
                        {[...Array(4)].map((_, i) => (
                            <div key={i} className="bg-white dark:bg-gray-800 rounded-lg shadow-sm p-4 border border-gray-200 dark:border-gray-700">
                                <div className="h-4 bg-gray-300 dark:bg-gray-600 rounded w-3/4 mb-2"></div>
                                <div className="h-6 bg-gray-300 dark:bg-gray-600 rounded w-1/2"></div>
                            </div>
                        ))}
                    </div>
                ) : summaryError && !summaryData ? (
                    <div className="p-3 bg-red-100 border border-red-300 text-red-800 rounded-md text-sm">
                        Error loading summary: {summaryError}
                    </div>
                ) : summaryData ? (
                    <>
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                            {/* Stat Card: Active Agents */}
                            <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm p-4 border border-gray-200 dark:border-gray-700 flex items-center gap-3">
                                <Users className="w-6 h-6 text-blue-500 flex-shrink-0" />
                                <div>
                                    <div className="text-sm text-gray-500 dark:text-gray-400">Active Agents</div>
                                    <div className="text-xl font-bold text-gray-900 dark:text-gray-100">{summaryData.total_agents_active ?? 'N/A'}</div>
                                </div>
                            </div>
                            {/* Stat Card: Agents with Alerts */}
                            <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm p-4 border border-gray-200 dark:border-gray-700 flex items-center gap-3">
                                <AlertCircle className={`w-6 h-6 flex-shrink-0 ${summaryData.agents_with_alerts > 0 ? 'text-yellow-500' : 'text-green-500'}`} />
                                <div>
                                    <div className="text-sm text-gray-500 dark:text-gray-400">Agents w/ Alerts</div>
                                    <div className={`text-xl font-bold ${summaryData.agents_with_alerts > 0 ? 'text-yellow-600 dark:text-yellow-400' : 'text-gray-900 dark:text-gray-100'}`}>
                                        {summaryData.agents_with_alerts ?? 'N/A'}
                                    </div>
                                </div>
                            </div>
                            {/* Stat Card: Total Network Throughput */}
                            <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm p-4 border border-gray-200 dark:border-gray-700 flex items-center gap-3">
                                <BarChart2 className="w-6 h-6 text-indigo-500 flex-shrink-0" />
                                <div>
                                    <div className="text-sm text-gray-500 dark:text-gray-400">Total Network</div>
                                    <div className="text-xl font-bold text-gray-900 dark:text-gray-100">
                                        {(summaryData.total_network_throughput_mbps ?? -1) >= 0
                                            ? `${summaryData.total_network_throughput_mbps.toFixed(1)} Mbps`
                                            : 'N/A'}
                                    </div>
                                </div>
                            </div>
                            {/* Stat Card: Total Peer Traffic */}
                            <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm p-4 border border-gray-200 dark:border-gray-700 flex items-center gap-3">
                                <GitBranch className="w-6 h-6 text-purple-500 flex-shrink-0" />
                                <div>
                                    <div className="text-sm text-gray-500 dark:text-gray-400">Detected Peer Rate*</div>
                                    <div className="text-xl font-bold text-gray-900 dark:text-gray-100">
                                        {(summaryData.total_peer_traffic_mbps ?? -1) >= 0
                                            ? `${summaryData.total_peer_traffic_mbps.toFixed(2)} Mbps`
                                            : 'N/A'}
                                    </div>
                                </div>
                            </div>
                        </div>

                        {/* Always show footnote when summary data exists */}
                        <p className="text-gray-400 dark:text-gray-500 text-[10px] italic mt-1.5 text-right pr-1">
                            *Peer rates estimated via sampling, may be lower than actual under high load.
                        </p>
                    </>
                ) : null /* Should not happen if loading/error handled */}
                {/* Display non-blocking summary error if data exists */}
                {summaryError && summaryData && (
                    <div className="mt-2 p-2 bg-yellow-100 border border-yellow-300 text-yellow-800 rounded-md text-xs">
                        Warning: Could not update summary data. Last fetch failed: {summaryError}
                    </div>
                )}
            </div>
            {/* --- END Summary Stats Section --- */}

            {/* Display non-blocking error if data exists but fetch failed */}
            {error && Object.keys(hosts).length > 0 && (
                <div className="mb-4 p-3 bg-yellow-100 border border-yellow-300 text-yellow-800 rounded-md text-sm">
                    Warning: Could not update data. Last fetch failed: {error}
                </div>
            )}
            {/* Host Card Grid */}
            {filteredAndSortedHosts.length > 0 ? (
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5 gap-4 sm:gap-6">
                    {filteredAndSortedHosts.map(([hostname, hostData]: [string, HostData]) => (
                        <HostCard
                            key={hostname}
                            hostname={hostname}
                            data={hostData.data}
                            onSelect={() => handleSelectHost(hostname)}
                            sortConfig={sortConfig}
                            onSort={handleSort}
                        />
                    ))}
                </div>
            ) : (
                <div className="text-center py-10 text-gray-500 dark:text-gray-400">
                    {Object.keys(hosts).length === 0 && !searchTerm ? "No active hosts found." :
                        searchTerm && !filterByAlert ? `No hosts found matching "${searchTerm}".` :
                            filterByAlert && !searchTerm ? "No hosts with active alerts." :
                                `No hosts matching filters.` // Combined case
                    }
                </div>
            )}
            {/* Modal */}
            {selectedHost && hosts[selectedHost] && (
                <HostDetailModal
                    hostname={selectedHost}
                    data={hosts[selectedHost]?.data}
                    onClose={handleCloseModal}
                />
            )}
            {/* Footer Timestamp */}
            <div className="text-center text-gray-500 text-xs sm:text-sm mt-8">
                Dashboard Time: {new Date().toLocaleTimeString()} (Data updates every {FETCH_INTERVAL_MS / 1000}s)
            </div>
        </div>
    );
}


// ========================================================================
//             Main App Component (Layout & Routing)
// ========================================================================
function App() {
    const location = useLocation(); // Get current location for active link styling

    // --- NEW: State for Alerts ---
    const [alerts, setAlerts] = useState<AlertInfo[]>([]);

    // --- NEW: Function to add an alert (prevent duplicates) ---
    const addAlert = useCallback((newAlert: Omit<AlertInfo, 'timestamp'>) => {
        setAlerts(prevAlerts => {
            // Check if an alert with the same ID already exists
            if (!prevAlerts.some(alert => alert.id === newAlert.id)) {
                // Add new alert with timestamp
                return [...prevAlerts, { ...newAlert, timestamp: Date.now() }];
            }
            // Otherwise, return previous state (no duplicate added)
            return prevAlerts;
        });
    }, []);

    // --- Function to remove/acknowledge an alert ---
    const dismissAlert = useCallback((alertId: string) => {
        setAlerts(prevAlerts => prevAlerts.filter(alert => alert.id !== alertId));
        // Or mark as acknowledged:
        // setAlerts(prevAlerts => prevAlerts.map(alert =>
        //    alert.id === alertId ? { ...alert, acknowledged: true } : alert
        // ));
    }, []);

    return (
        // Overall page layout with sidebar and main content area
        <div className="min-h-screen bg-gray-100 dark:bg-gray-900 flex flex-col sm:flex-row relative">
            <AlertPanel alerts={alerts} dismissAlert={dismissAlert} />

            {/* Sidebar Navigation */}
            <nav className="bg-white w-full sm:w-56 flex-shrink-0 border-b sm:border-b-0 sm:border-r border-gray-200 shadow-sm sm:shadow-none z-10"> {/* Added z-index */}
                <div className="p-4 sticky top-0"> {/* Make sidebar content sticky */}
                    <h2 className="text-xl font-semibold text-gray-800 mb-6 px-3">Monitor</h2> {/* Added padding */}
                    <ul className="space-y-2">
                        <li>
                            {/* Dashboard Link */}
                            <Link
                                to="/"
                                className={`flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors duration-150 ease-in-out ${location.pathname === '/'
                                    ? 'bg-blue-50 text-blue-700'
                                    : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'
                                    }`}
                            >
                                <LayoutDashboard className="w-5 h-5" />
                                Dashboard
                            </Link>
                        </li>
                        <li>
                            {/* Peer Traffic Link */}
                            <Link
                                to="/peer-traffic"
                                className={`flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors duration-150 ease-in-out ${location.pathname === '/peer-traffic'
                                    ? 'bg-blue-50 text-blue-700'
                                    : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'
                                    }`}
                            >
                                <Network className="w-5 h-5" />
                                Peer Traffic
                            </Link>
                        </li>
                        <li>
                            <Link
                                to="/history"
                                className={`flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors duration-150 ease-in-out ${location.pathname === '/history'
                                    ? 'bg-blue-50 text-blue-700'
                                    : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'
                                    }`}
                            >
                                {/* Use renamed HistoryIcon */}
                                <HistoryIcon className="w-5 h-5" />
                                History / Trends
                            </Link>
                        </li>
                        <li>
                            <Link
                                to="/alerts"
                                className={`flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors duration-150 ease-in-out ${location.pathname === '/alerts'
                                    ? 'bg-blue-50 text-blue-700'
                                    : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'
                                    }`}
                            >
                                <Bell className="w-5 h-5" />
                                Alerts
                            </Link>
                        </li>
                        <li>
                            <Link
                                to="/network-focus"
                                className={`flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors duration-150 ease-in-out ${
                                    location.pathname === '/network-focus'
                                        ? 'bg-blue-50 text-blue-700'
                                        : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'
                                    }`}
                            >
                                {/* Use appropriate icon */}
                                <Share2 className="w-5 h-5" />
                                Network Focus
                            </Link>
                        </li>
                    </ul>
                </div>
            </nav>

            {/* Main Content Area */}
            {/* Ensure this area scrolls independently if content exceeds viewport height */}
            <main className="flex-grow overflow-hidden bg-gray-50 relative"> {/* Changed background */}
                {/* Define the routes and which component renders for each path */}
                <Routes>
                    {/* Route for the dashboard - Pass addAlert function */}
                    <Route path="/" element={<Dashboard addAlert={addAlert} />} />
                    {/* Route for the peer traffic page */}
                    <Route path="/peer-traffic" element={<PeerTrafficPage />} />
                    {/* --- NEW: History Route --- */}
                    <Route path="/history" element={<HistoryPage />} />
                    <Route path="/alerts" element={<AlertsPage />} />
                    <Route path="/network-focus" element={<NetworkFocusPage />} />
                    {/* Catch-all route for 404 Not Found */}
                    <Route path="*" element={<div className='p-6 text-center text-gray-600'><h2>404 - Page Not Found</h2></div>} />
                </Routes>
            </main>
            <ToastContainer
                position="bottom-right" // Or "top-right", "top-center", etc.
                autoClose={8000} // Auto close after 8 seconds
                hideProgressBar={false}
                newestOnTop={false}
                closeOnClick
                rtl={false}
                pauseOnFocusLoss
                draggable
                pauseOnHover
                theme="colored" // Use colored themes based on type (info, success, warning, error)
            // limit={3} // Optional: Limit number of visible toasts
            />
        </div>
    );
}

export default App;