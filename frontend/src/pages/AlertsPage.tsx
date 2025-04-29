// src/pages/AlertsPage.tsx
import { useState, useEffect, useCallback, useMemo } from 'react';
import {
    AlertTriangle, ArrowUpDown, ArrowUp, ArrowDown, Loader2,
    ShieldCheck, Info, Server, Type, X // Added X for potentially clearing filters later
} from 'lucide-react';
import { format, formatDistanceToNow } from 'date-fns';

// --- Types ---
// Define a more specific type for alerts coming from the API
// Adjust based on the actual fields returned by /api/alerts
interface ApiAlert {
    hostname: string;
    alert_type: string; // 
    specific_target: string | null; 
    status: 'active' | 'resolved';
    message: string;
    current_value: number | string | null; 
    threshold_value: number | string | null; 
    first_triggered_unix: number; 
    last_active_unix: number; 
    resolved_unix: number | null; // 
    // id?: number; // Optional ID from DB
}

// Sorting types - update keys to match ApiAlert
type AlertSortKey = 'hostname' | 'alert_type' | 'status' | 'first_triggered_unix' | 'last_active_unix' | 'resolved_unix' | 'message' | 'specific_target'; // Added/updated keys
type AlertSortOrder = 'asc' | 'desc';

// --- Type for Status Filter ---
type AlertStatusFilter = 'active' | 'resolved' | 'all';

// --- Helper Function ---
function formatTimestamp(unixTimestamp: number | null): string {
    if (unixTimestamp === null || typeof unixTimestamp === 'undefined') return 'N/A'; // Check null/undefined
    try {
        return format(new Date(unixTimestamp * 1000), 'yyyy-MM-dd HH:mm:ss');
    } catch (e) { return 'Invalid Date'; }
}
function formatTimeAgo(unixTimestamp: number | null): string {
     if (unixTimestamp === null || typeof unixTimestamp === 'undefined') return 'N/A'; // Check null/undefined
     try {
        return formatDistanceToNow(new Date(unixTimestamp * 1000), { addSuffix: true });
     } catch (e) { return 'Invalid Date'; }
 }


// --- Component ---
function AlertsPage() {
    // --- Existing State ---
    const [alerts, setAlerts] = useState<ApiAlert[]>([]); // Holds ALL alerts fetched for the current status filter
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [sortConfig, setSortConfig] = useState<{ key: AlertSortKey; order: AlertSortOrder }>({ key: 'last_active_unix', order: 'desc' });
    const [statusFilter, setStatusFilter] = useState<AlertStatusFilter>('active');

    // --- NEW: State for Table Filters ---
    const [hostnameFilter, setHostnameFilter] = useState('');
    const [alertTypeFilter, setAlertTypeFilter] = useState('');


    // --- Fetch Data (Keep existing - fetches based on statusFilter) ---
    const fetchAlerts = useCallback(async (filter: AlertStatusFilter) => {
        setError(null);
        try {
            // Include status filter in API call
            const response = await fetch(`/api/alerts?status=${filter}`);
            if (!response.ok) {
                throw new Error(`Failed to fetch alerts: ${response.status}`);
            }
            const data: ApiAlert[] = await response.json();
            setAlerts(data);
        } catch (e) {
            console.error("Error fetching alerts:", e);
            setError(e instanceof Error ? e.message : "Unknown error fetching alerts");
        } finally {
            setLoading(false);
        }
    }, []);

    // Fetch on mount and set interval
    useEffect(() => {
        setLoading(true); // Set loading true when filter changes or on mount
        fetchAlerts(statusFilter);

        const interval = setInterval(() => {
            // Refresh data based on current filter
            fetchAlerts(statusFilter);
        }, 5000); // Refresh alerts every 5s

        return () => clearInterval(interval);
    }, [fetchAlerts, statusFilter]); // Re-fetch when filter changes


    // --- Sorting Handler (Keep existing) ---
    const handleSort = useCallback((key: AlertSortKey) => {
        setSortConfig(prev => ({
            key,
            order: prev.key === key && prev.order === 'desc' ? 'asc' : 'desc'
        }));
    }, []);


    // --- MODIFIED: Apply Filtering BEFORE Sorting ---
    const filteredAndSortedAlerts = useMemo(() => {
        // Apply text filters first
        const filtered = alerts.filter(alert => {
            const hostMatch = !hostnameFilter || alert.hostname.toLowerCase().includes(hostnameFilter.toLowerCase());
            const typeMatch = !alertTypeFilter || alert.alert_type.toLowerCase().includes(alertTypeFilter.toLowerCase());
            return hostMatch && typeMatch;
        });

        // Then sort the filtered results
        const sortableAlerts = [...filtered];
        sortableAlerts.sort((a, b) => {
             // --- Keep existing sorting logic ---
             const key = sortConfig.key;
             const orderMultiplier = sortConfig.order === 'asc' ? 1 : -1;

             let valA = (key === 'resolved_unix' || key === 'last_active_unix' || key === 'first_triggered_unix')
                         ? (a[key] ?? (orderMultiplier === 1 ? Infinity : -Infinity)) // Handle null timestamps for sorting
                         : a[key];
             let valB = (key === 'resolved_unix' || key === 'last_active_unix' || key === 'first_triggered_unix')
                         ? (b[key] ?? (orderMultiplier === 1 ? Infinity : -Infinity)) // Handle null timestamps for sorting
                         : b[key];

             // Handle different types for comparison
             if (typeof valA === 'string' && typeof valB === 'string') {
                 return valA.localeCompare(valB) * orderMultiplier;
             }
             if (typeof valA === 'number' && typeof valB === 'number') {
                 return (valA - valB) * orderMultiplier;
             }
             if (key === 'specific_target') {
                 const strA = String(valA ?? ''); // Convert null to empty string for comparison
                 const strB = String(valB ?? '');
                 return strA.localeCompare(strB) * orderMultiplier;
              }
             // Fallback comparison (e.g., treat nulls/undefined consistently)
             if (valA == null && valB != null) return -1 * orderMultiplier;
             if (valA != null && valB == null) return 1 * orderMultiplier;

             return 0; // Treat as equal if types mismatch or both null
         });
        return sortableAlerts;
        // Add filter states to dependency array
    }, [alerts, sortConfig, hostnameFilter, alertTypeFilter]); // <-- Added filters


    // Helper to render sort icon (Keep existing)
    const getSortIcon = (key: AlertSortKey) => {
       if (sortConfig.key !== key) return <ArrowUpDown className="inline-block ml-1 h-3 w-3 text-gray-400" />;
       return sortConfig.order === 'asc'
           ? <ArrowUp className="inline-block ml-1 h-3 w-3 text-blue-600" />
           : <ArrowDown className="inline-block ml-1 h-3 w-3 text-blue-600" />;
   };


    return (
        <div className="p-4 sm:p-6 h-full flex flex-col">
            {/* Header and Status Filter */}
            {/* --- Added border-b --- */}
            <div className="flex flex-col sm:flex-row justify-between items-center mb-4 gap-3 border-b dark:border-gray-700 pb-3">
                <h1 className="text-2xl sm:text-3xl font-bold text-gray-900 dark:text-gray-100">
                    Alerts Dashboard
                </h1>
                {/* Status Filter Buttons (Keep existing structure) */}
                <div className="flex items-center space-x-2 border border-gray-300 dark:border-gray-600 rounded-lg p-1 bg-gray-100 dark:bg-gray-700">
                    {(['active', 'resolved', 'all'] as AlertStatusFilter[]).map((status) => (
                        <button
                            key={status}
                            onClick={() => setStatusFilter(status)}
                            className={`px-3 py-1 rounded-md text-sm font-medium transition-colors ${
                                statusFilter === status
                                    ? 'bg-white dark:bg-gray-900 text-blue-600 dark:text-blue-400 shadow'
                                    : 'text-gray-600 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-600'
                            }`}
                        >
                            {status.charAt(0).toUpperCase() + status.slice(1)}
                        </button>
                    ))}
                </div>
            </div>

            {/* --- NEW: Filter Input Row --- */}
            <div className="mb-4 flex flex-col sm:flex-row gap-4 px-1">
                {/* Hostname Filter */}
                <div className="relative flex-grow">
                     <label htmlFor="hostname-filter" className="sr-only">Filter by Hostname</label>
                     <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                        <Server className="h-4 w-4 text-gray-400" aria-hidden="true" />
                     </div>
                    <input
                        type="text"
                        id="hostname-filter"
                        placeholder="Filter by Hostname..."
                        value={hostnameFilter}
                        onChange={(e) => setHostnameFilter(e.target.value)}
                        className="block w-full pl-10 pr-3 py-1.5 text-sm rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 placeholder-gray-400 dark:placeholder-gray-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 focus:border-indigo-500"
                    />
                        {hostnameFilter && (
                        <button onClick={() => setHostnameFilter('')} className="absolute inset-y-0 right-0 pr-3 flex items-center text-gray-400 hover:text-gray-600">
                            <X size={14} />
                        </button>
                    )}
                </div>
                {/* Alert Type Filter */}
                 <div className="relative flex-grow">
                     <label htmlFor="type-filter" className="sr-only">Filter by Alert Type</label>
                     <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                        <Type className="h-4 w-4 text-gray-400" aria-hidden="true" />
                     </div>
                    <input
                        type="text"
                        id="type-filter"
                        placeholder="Filter by Type (e.g., cpu, down)..."
                        value={alertTypeFilter}
                        onChange={(e) => setAlertTypeFilter(e.target.value)}
                        className="block w-full pl-10 pr-3 py-1.5 text-sm rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 placeholder-gray-400 dark:placeholder-gray-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 focus:border-indigo-500"
                     />
                </div>
            </div>
            {/* --- END Filter Input Row --- */}


            {/* Table Area */}
            {/* --- Changed to overflow-auto --- */}
            <div className="flex-grow overflow-auto">
                {/* Loading State (Keep existing) */}
                 {loading && (
                    <div className="flex items-center justify-center h-full text-gray-500 dark:text-gray-400">
                         <Loader2 size={24} className="animate-spin mr-2"/> Loading alerts...
                    </div>
                )}
                {/* Error State (Keep existing) */}
                {error && !loading && (
                    <div className="flex items-center justify-center h-full text-red-500 px-4 py-2 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-500/30 rounded">
                         <AlertTriangle size={20} className="mr-2"/> Error loading alerts: {error}
                    </div>
                )}

                {/* Empty State (Modified) */}
                {!loading && !error && filteredAndSortedAlerts.length === 0 && (
                    <div className="flex items-center justify-center h-full text-gray-500 dark:text-gray-400 italic">
                         {/* Conditionally render icon and message based on state */}
                         {alerts.length > 0 ? (
                             // Case: Filters applied, no results (Use Info icon, default gray text)
                             <>
                                 <Info size={20} className="mr-2"/>
                                 {'No alerts match current filters.'}
                             </>
                         ) : statusFilter === 'active' ? (
                             // Case: No active alerts found (Use ShieldCheck icon, green text)
                             <>
                                 <ShieldCheck size={20} className="mr-2 text-green-500"/> {/* Green Shield Icon */}
                                 {/* Apply green text color specifically to this message */}
                                 <span className="text-green-600 dark:text-green-400 font-medium not-italic">
                                     {'No active alerts found. System looks healthy!'}
                                 </span>
                             </>
                         ) : (
                             // Case: No resolved/all alerts found (Use Info icon, default gray text)
                             <>
                                  <Info size={20} className="mr-2"/>
                                  {`No ${statusFilter} alerts found.`}
                             </>
                         )}
                    </div>
                )}

                {/* Table (Use filteredAndSortedAlerts) */}
                {!loading && !error && filteredAndSortedAlerts.length > 0 && (
                    // Added overflow-hidden to container
                    <div className="border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden shadow-sm">
                        <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
                            {/* Table Head (Keep existing structure) */}
                             <thead className="bg-gray-50 dark:bg-gray-700 sticky top-0 z-10">
                                <tr>
                                    {/* Status Column */}
                                    <th scope="col" className="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase tracking-wider cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-600" onClick={() => handleSort('status')}>
                                         Status {getSortIcon('status')}
                                     </th>
                                     {/* Last Active Column */}
                                    <th scope="col" className="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase tracking-wider cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-600" onClick={() => handleSort('last_active_unix')}>
                                        Last Active {getSortIcon('last_active_unix')}
                                    </th>
                                    {/* Hostname Column */}
                                    <th scope="col" className="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase tracking-wider cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-600" onClick={() => handleSort('hostname')}>
                                        Hostname {getSortIcon('hostname')}
                                    </th>
                                    {/* Type Column */}
                                    <th scope="col" className="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase tracking-wider cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-600" onClick={() => handleSort('alert_type')}>
                                        Type {getSortIcon('alert_type')}
                                    </th>
                                    {/* Target Column */}
                                     <th scope="col" className="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase tracking-wider cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-600" onClick={() => handleSort('specific_target')}>
                                         Target {getSortIcon('specific_target')}
                                     </th>
                                    {/* Message Column */}
                                    <th scope="col" className="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase tracking-wider cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-600" onClick={() => handleSort('message')}>
                                        Message {getSortIcon('message')}
                                    </th>
                                    {/* Resolved Column (Conditional) */}
                                     {statusFilter !== 'active' && ( // Show only if viewing resolved or all
                                         <th scope="col" className="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase tracking-wider cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-600" onClick={() => handleSort('resolved_unix')}>
                                             Resolved {getSortIcon('resolved_unix')}
                                         </th>
                                     )}
                                </tr>
                            </thead>
                            {/* Table Body (Map over filteredAndSortedAlerts) */}
                            <tbody className="bg-white dark:bg-gray-800 divide-y divide-gray-200 dark:divide-gray-700">
                                {filteredAndSortedAlerts.map((alert, index) => (
                                    // Use corrected field names from ApiAlert for key
                                    <tr key={`${alert.hostname}-${alert.alert_type}-${alert.specific_target || index}-${alert.first_triggered_unix}`} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                                        {/* Status Cell */}
                                        <td className="px-4 py-2 whitespace-nowrap text-sm">
                                            <span className={`px-2 inline-flex text-xs leading-5 font-semibold rounded-full ${
                                                alert.status === 'active' ? 'bg-red-100 text-red-800 dark:bg-red-900/50 dark:text-red-300'
                                                                          : 'bg-green-100 text-green-800 dark:bg-green-900/50 dark:text-green-300'
                                            }`}>
                                                {alert.status}
                                            </span>
                                        </td>
                                        {/* Last Active Cell */}
                                        <td className="px-4 py-2 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400" title={formatTimestamp(alert.last_active_unix)}>
                                            {formatTimeAgo(alert.last_active_unix)}
                                        </td>
                                        {/* Hostname Cell */}
                                        <td className="px-4 py-2 whitespace-nowrap text-sm font-medium text-gray-900 dark:text-gray-100">{alert.hostname}</td>
                                        {/* Type Cell */}
                                        <td className="px-4 py-2 whitespace-nowrap text-sm text-gray-700 dark:text-gray-300">
                                            <span className={`px-2 inline-flex text-xs leading-5 font-semibold rounded-full ${
                                                alert.alert_type?.toLowerCase().includes('down') ? 'bg-red-100 text-red-800 dark:bg-red-900/50 dark:text-red-300' :
                                                alert.alert_type?.toLowerCase().includes('choked') || alert.alert_type?.toLowerCase().includes('network') ? 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/50 dark:text-yellow-300' :
                                                alert.alert_type?.toLowerCase().includes('cpu') ? 'bg-blue-100 text-blue-800 dark:bg-blue-900/50 dark:text-blue-300' :
                                                alert.alert_type?.toLowerCase().includes('memory') ? 'bg-purple-100 text-purple-800 dark:bg-purple-900/50 dark:text-purple-300' :
                                                alert.alert_type?.toLowerCase().includes('disk') ? 'bg-indigo-100 text-indigo-800 dark:bg-indigo-900/50 dark:text-indigo-300' :
                                                'bg-orange-100 text-orange-800 dark:bg-orange-900/50 dark:text-orange-300' // Default/Resource/Other
                                            }`}>
                                                {alert.alert_type || 'Unknown'}
                                            </span>
                                        </td>
                                        {/* Target Cell */}
                                        <td className="px-4 py-2 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400">{alert.specific_target || '-'}</td>
                                        {/* Message Cell */}
                                        <td className="px-4 py-2 text-sm text-gray-700 dark:text-gray-300 max-w-xs truncate" title={alert.message}>{alert.message}</td>
                                        {/* Resolved Cell (Conditional) */}
                                        {statusFilter !== 'active' && ( // Show only if viewing resolved or all
                                            <td className="px-4 py-2 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400" title={formatTimestamp(alert.resolved_unix)}>
                                                {/* Check status AND resolved_unix before formatting */}
                                                {alert.status === 'resolved' ? formatTimeAgo(alert.resolved_unix) : '-'}
                                            </td>
                                        )}
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>
        </div>
    );
}

export default AlertsPage;