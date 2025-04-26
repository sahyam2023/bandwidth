// src/pages/HistoryPage.tsx
import { useState, useEffect, useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import Select, { MultiValue } from 'react-select';
import DatePicker from 'react-datepicker';
import "react-datepicker/dist/react-datepicker.css";
import { subHours, formatISO } from 'date-fns';

import { Line } from 'react-chartjs-2';
import {
    Chart as ChartJS, LinearScale, TimeScale, PointElement, LineElement, Title, Tooltip, Legend, Filler,
    ChartOptions
} from 'chart.js';
import 'chartjs-adapter-date-fns';
import { parseISO } from 'date-fns';

ChartJS.register(
    LinearScale,
    TimeScale,
    PointElement,
    LineElement,
    Title,
    Tooltip,
    Legend,
    Filler
);

import { HostData } from '../types';

interface SelectOption {
    value: string;
    label: string;
}

interface HistoryApiResponse {
    timestamps: string[];
    results: {
        [hostname: string]: {
            [metricPath: string]: (number | string | null)[];
        };
    };
}

// Allow y to be null in our Point representation for Chart.js data
interface ChartPoint {
    x: number;
    y: number | null;
}

interface ChartDataset {
    label: string;
    data: ChartPoint[];
    borderColor: string;
    backgroundColor: string;
    tension?: number;
    fill?: boolean;
    yAxisID?: string;
    spanGaps?: boolean; // Added spanGaps for type safety
}

interface FormattedChartData {
    // No need for labels with time scale
    datasets: ChartDataset[];
}

const metricOptions: SelectOption[] = [
    { value: 'cpu_percent', label: 'CPU Usage (%)' },
    { value: 'mem_percent', label: 'Memory Usage (%)' },
    { value: 'network_total_sent_mbps', label: 'Network Total Sent (Mbps)' },
    { value: 'network_total_recv_mbps', label: 'Network Total Received (Mbps)' },
    { value: 'network_interfaces.Ethernet.sent_Mbps', label: 'Net: Ethernet Sent (Mbps)' },
    { value: 'network_interfaces.Ethernet.recv_Mbps', label: 'Net: Ethernet Recv (Mbps)' },
    { value: 'disk_io.PhysicalDrive0.read_ops_ps', label: 'DiskIO: PhysicalDrive0 Read (IOPS)' },
    { value: 'disk_io.PhysicalDrive0.write_ops_ps', label: 'DiskIO: PhysicalDrive0 Write (IOPS)' },
];

function HistoryPage() {
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const [availableHosts, setAvailableHosts] = useState<SelectOption[]>([]);
    const [selectedHosts, setSelectedHosts] = useState<MultiValue<SelectOption>>([]);
    const [hostsLoading, setHostsLoading] = useState(true);
    const [hostsError, setHostsError] = useState<string | null>(null);

    const [selectedMetrics, setSelectedMetrics] = useState<MultiValue<SelectOption>>([]);

    // Modified: Time Range State to allow null
    const [startTime, setStartTime] = useState<Date | null>(subHours(new Date(), 1));
    const [endTime, setEndTime] = useState<Date | null>(new Date());
    const [searchParams, setSearchParams] = useSearchParams();

    const [chartData, setChartData] = useState<FormattedChartData | null>(null);

    const fetchAvailableHosts = useCallback(async () => {
        setHostsLoading(true);
        setHostsError(null);
        try {
            const response = await fetch('/api/latest_data');
            if (!response.ok) { throw new Error(`Failed to fetch hosts: ${response.status}`); }
            const data: Record<string, HostData> = await response.json();
            const hostOptions = Object.keys(data)
                .sort()
                .map(hostname => ({ value: hostname, label: hostname }));
            setAvailableHosts(hostOptions);
        } catch (e) {
            console.error("Error fetching available hosts:", e);
            setHostsError(e instanceof Error ? e.message : "Unknown error fetching hosts");
        } finally {
            setHostsLoading(false);
        }
    }, []);

    useEffect(() => { fetchAvailableHosts(); }, [fetchAvailableHosts]);
    useEffect(() => {
        const hostnameFromUrl = searchParams.get('hostname');

        // Only run if hostname exists in URL AND hosts are loaded
        if (hostnameFromUrl && availableHosts.length > 0) {
            // Check if THIS specific hostname isn't already selected
            // Prevents loop if user deselects then reselects manually while param is still there briefly
            const isAlreadySelected = selectedHosts.some(h => h.value === hostnameFromUrl);

            if (!isAlreadySelected) {
                 const matchingOption = availableHosts.find(opt => opt.value === hostnameFromUrl);
                 if (matchingOption) {
                    console.log(`Pre-selecting host from URL: ${hostnameFromUrl}`);
                    setSelectedHosts([matchingOption]); // Select the matching host

                    // --- NEW: Remove the parameter from URL ---
                    // Use replace: true to avoid adding a new entry in browser history
                    setSearchParams({}, { replace: true });
                    // --- END NEW ---

                } else {
                    console.warn(`Hostname "${hostnameFromUrl}" from URL query parameter not found in available hosts.`);
                    // Optionally clear param if host not found too?
                    // setSearchParams({}, { replace: true });
                }
            }
        }
        // Only depend on availableHosts and searchParams initially.
        // Avoid selectedHosts here to prevent re-running after manual selection.
    }, [availableHosts, searchParams, setSearchParams]);

    // Modified: transformApiDataToChartData with robust timestamp handling
    const transformApiDataToChartData = (apiData: HistoryApiResponse, sHosts: readonly SelectOption[], sMetrics: readonly SelectOption[]): FormattedChartData | null => {
        if (!apiData || !apiData.timestamps || apiData.timestamps.length === 0 || !apiData.results) {
            return null;
        }

        const datasets: ChartDataset[] = [];
        const colors = ['#3B82F6', '#10B981', '#EF4444', '#F59E0B', '#8B5CF6', '#EC4899', '#6366F1', '#14B8A6'];
        let colorIndex = 0;

        // Try parsing all timestamps first to find valid range and map indices
        const validTimestamps: { date: Date, originalIndex: number }[] = [];
        apiData.timestamps.forEach((ts, index) => {
            try {
                const parsedDate = parseISO(ts);
                if (!isNaN(parsedDate.getTime())) {
                    validTimestamps.push({ date: parsedDate, originalIndex: index });
                }
            } catch (e) {
                console.warn(`Could not parse timestamp: ${ts}`);
            }
        });

        if (validTimestamps.length === 0) return null; // No valid timestamps

        sHosts.forEach(hostOption => {
            const hostname = hostOption.value;
            const hostResults = apiData.results[hostname];
            if (!hostResults) return;

            sMetrics.forEach(metricOption => {
                const metricPath = metricOption.value;
                const metricDataArray = hostResults[metricPath];

                if (metricDataArray && metricDataArray.length === apiData.timestamps.length) { // Check against original length
                    // Create Chart.js data points {x: timeMs, y: value} using only valid timestamps
                    const dataPoints: ChartPoint[] = validTimestamps.map(vt => {
                        const value = metricDataArray[vt.originalIndex]; // Get value using original index
                        const numericValue = (typeof value === 'number') ? value : null;
                        return {
                            x: vt.date.getTime(), // Use Date object ms for x-axis
                            y: numericValue,
                        };
                    });

                    if (dataPoints.some(p => p.y !== null)) { // Only add dataset if there's at least one non-null Y value
                        const color = colors[colorIndex % colors.length];
                        colorIndex++;
                        datasets.push({
                            label: `${hostname}: ${metricOption.label}`,
                            data: dataPoints,
                            borderColor: color,
                            backgroundColor: `${color}33`,
                            tension: 0.1,
                            fill: false,
                            yAxisID: 'y',
                            spanGaps: true // Important for handling nulls
                        });
                    }
                } else {
                    console.warn(`Data mismatch or missing for ${hostname} - ${metricPath}`);
                }
            });
        });
        // No labels needed for time scale
        return { datasets };
    };

    const handleFetchHistory = useCallback(async () => {
        if (selectedHosts.length === 0) { setError("Please select at least one host."); return; }
        if (selectedMetrics.length === 0) { setError("Please select at least one metric."); return; }
        if (!startTime || !endTime || startTime >= endTime) { setError("Invalid time range selected."); return; }

        const hostnamesParam = selectedHosts.map(h => h.value).join(',');
        const metricsParam = selectedMetrics.map(m => m.value).join(',');
        const startTimeParam = formatISO(startTime); // formatISO handles Date object directly
        const endTimeParam = formatISO(endTime);

        const apiUrl = `/api/history/range?hostnames=${encodeURIComponent(hostnamesParam)}&metrics=${encodeURIComponent(metricsParam)}&start_time=${encodeURIComponent(startTimeParam)}&end_time=${encodeURIComponent(endTimeParam)}`;

        setError(null);
        setLoading(true);
        setChartData(null);

        try {
            console.log("Fetching history from:", apiUrl);
            const response = await fetch(apiUrl);
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({ error: `HTTP error ${response.status}` }));
                throw new Error(errorData?.error || `Failed to fetch history: ${response.status}`);
            }
            const apiData: HistoryApiResponse = await response.json();

            // Pass readonly arrays
            const formattedData = transformApiDataToChartData(apiData, [...selectedHosts], [...selectedMetrics]);
            setChartData(formattedData);
            if (!formattedData || formattedData.datasets.length === 0) {
                setError("No data found for the selected criteria.");
            }
        } catch (e) {
            console.error("Error fetching history:", e);
            setError(e instanceof Error ? e.message : "Unknown error fetching history");
            setChartData(null);
        } finally {
            setLoading(false);
        }
    }, [selectedHosts, selectedMetrics, startTime, endTime]);

    const chartOptions = useMemo((): ChartOptions<'line'> => ({
        responsive: true,
        maintainAspectRatio: false,
        interaction: {
            mode: 'index' as const,
            intersect: false,
        },
        scales: {
            x: {
                type: 'time',
                time: {
                    tooltipFormat: 'yyyy-MM-dd HH:mm:ss',
                    unit: 'minute',
                    displayFormats: {
                        second: 'HH:mm:ss',
                        minute: 'HH:mm',
                        hour: 'HH:mm',
                        day: 'MMM d',
                    }
                },
                title: {
                    display: true,
                    text: 'Time',
                },
                ticks: {
                    source: 'auto',
                    maxRotation: 0,
                    autoSkip: true,
                }
            },
            y: {
                type: 'linear',
                beginAtZero: true,
                title: {
                    display: true,
                    text: 'Value',
                },
            }
        },
        plugins: {
            legend: {
                position: 'bottom' as const,
            },
            tooltip: {
                mode: 'index' as const,
                intersect: false,
            },
        },
        elements: {
            point: {
                radius: 0,
                hoverRadius: 5,
            },
            line: {
                borderWidth: 1.5,
            }
        }
    }), []);

    return (
        <div className="p-4 sm:p-6 h-full flex flex-col">
            <h1 className="text-2xl sm:text-3xl font-bold text-gray-900 dark:text-gray-100 mb-6">
                Historical Data & Trends
            </h1>

            {/* Controls Section */}
            <div className="mb-4 p-4 border rounded-lg bg-white dark:bg-gray-800 shadow-sm flex flex-col lg:flex-row gap-4 items-start">
                {/* Host Selector */}
                <div className="flex-1 w-full lg:w-auto lg:min-w-[200px]">
                    <label htmlFor="host-select" className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                        Select Host(s)
                    </label>
                    <Select<SelectOption, true>
                        instanceId="host-select"
                        isMulti
                        options={availableHosts}
                        value={selectedHosts}
                        onChange={setSelectedHosts}
                        isLoading={hostsLoading}
                        isDisabled={hostsLoading || !!hostsError}
                        placeholder={hostsLoading ? "Loading..." : hostsError ? "Error" : "Select..."}
                        className="react-select-container"
                        classNamePrefix="react-select"
                    />
                    {hostsError && <p className="text-xs text-red-500 mt-1">{hostsError}</p>}
                </div>

                {/* Metric Selector */}
                <div className="flex-1 w-full lg:w-auto lg:min-w-[200px]">
                    <label htmlFor="metric-select" className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                        Select Metric(s)
                    </label>
                    <Select<SelectOption, true>
                        instanceId="metric-select"
                        isMulti
                        options={metricOptions}
                        value={selectedMetrics}
                        onChange={setSelectedMetrics}
                        placeholder={"Select metrics..."}
                        className="react-select-container"
                        classNamePrefix="react-select"
                    />
                </div>

                {/* Time Range Pickers */}
                <div className="flex-1 w-full lg:w-auto flex flex-col sm:flex-row gap-2">
                    {/* Start Time */}
                    <div className="flex-1">
                        <label htmlFor="start-time" className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                            Start Time
                        </label>
                        <DatePicker
                            id="start-time"
                            selected={startTime}
                            onChange={(date: Date | null) => setStartTime(date)}
                            selectsStart
                            startDate={startTime}
                            endDate={endTime}
                            maxDate={endTime || undefined} // Convert null to undefined
                            showTimeSelect
                            timeFormat="HH:mm"
                            timeIntervals={15}
                            timeCaption="Time"
                            dateFormat="yyyy-MM-dd HH:mm"
                            className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100"
                        />

                        <DatePicker
                            id="end-time"
                            selected={endTime}
                            onChange={(date: Date | null) => setEndTime(date)}
                            selectsEnd
                            startDate={startTime}
                            endDate={endTime}
                            minDate={startTime || undefined} // Convert null to undefined
                            maxDate={new Date()} // This is already a Date, so no change needed
                            showTimeSelect
                            timeFormat="HH:mm"
                            timeIntervals={15}
                            timeCaption="Time"
                            dateFormat="yyyy-MM-dd HH:mm"
                            className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100"
                        />
                    </div>
                </div>

                {/* Fetch Button */}
                <div className="pt-5 self-end">
                    <button
                        onClick={handleFetchHistory}
                        disabled={loading || selectedHosts.length === 0 || selectedMetrics.length === 0 || !startTime || !endTime || startTime >= endTime}
                        className="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap"
                    >
                        {loading ? 'Loading...' : 'Fetch History'}
                    </button>
                </div>
            </div>

            {/* Chart Area */}
            <div className="flex-grow p-1 md:p-4 border rounded-lg bg-white dark:bg-gray-800 shadow-sm flex flex-col min-h-[400px]">
                {loading && (
                    <div className="flex-grow flex items-center justify-center">
                        <p className="text-gray-500 dark:text-gray-400 animate-pulse">Loading chart data...</p>
                    </div>
                )}
                {error && !loading && (
                    <div className="flex-grow flex items-center justify-center">
                        <p className="text-red-500 px-4 py-2 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-500/30 rounded">Error: {error}</p>
                    </div>
                )}
                {!loading && !error && !chartData && (
                    <div className="flex-grow flex items-center justify-center">
                        <p className="text-gray-500 dark:text-gray-400 italic">Select hosts, metrics, and time range, then click "Fetch History".</p>
                    </div>
                )}
                {/* Render Chart */}
                {!loading && !error && chartData && chartData.datasets.length > 0 && (
                    <div className="relative w-full h-full">
                        {/* Type assertion helps Chart.js handle our data structure */}
                        <Line options={chartOptions} data={chartData as any} />
                    </div>
                )}
            </div>
        </div>
    );
}

export default HistoryPage;