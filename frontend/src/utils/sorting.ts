// src/utils/sorting.ts

import { HostData, SortKey, SortOrder, DiskIoStats } from '../types'; // Import necessary types

// Helper function to calculate total IOPS (copied from previous suggestion)
const calculateTotalIops = (diskIo: Record<string, DiskIoStats> | undefined): number => {
  if (!diskIo || typeof diskIo !== 'object') {
    return -1; // Indicate no data or invalid data
  }
  let totalRead = 0;
  let totalWrite = 0;
  Object.values(diskIo).forEach((stats) => {
    if (stats && typeof stats === 'object') {
      totalRead += stats.read_ops_ps ?? 0;
      totalWrite += stats.write_ops_ps ?? 0;
    }
  });
  // Only return a non-negative value if there was actual data processed
  return (Object.keys(diskIo).length > 0) ? totalRead + totalWrite : -1;
};


export const sortHosts = (
  a: HostData,
  b: HostData,
  key: SortKey,
  order: SortOrder
): number => {
  const aData = a.data;
  const bData = b.data;
  const orderMultiplier = order === 'asc' ? 1 : -1;

  let valA: string | number;
  let valB: string | number;

  // Get the values based on the key, handling special cases and defaults
  switch (key) {
    case 'hostname':
      // Use lowercase for case-insensitive string comparison
      valA = aData.hostname?.toLowerCase() ?? ''; // Default to empty string if null/undefined
      valB = bData.hostname?.toLowerCase() ?? '';
      break;
    case 'cpu_percent':
      valA = aData.cpu_percent ?? -1; // Default N/A to -1 for sorting
      valB = bData.cpu_percent ?? -1;
      break;
    case 'mem_percent':
      valA = aData.mem_percent ?? -1;
      valB = bData.mem_percent ?? -1;
      break;
    case 'total_throughput_mbps':
      valA = aData.total_throughput_mbps ?? -1;
      valB = bData.total_throughput_mbps ?? -1;
      break;
    case 'disk_total_iops':
      valA = calculateTotalIops(aData.disk_io); // Use the helper function
      valB = calculateTotalIops(bData.disk_io);
      break;
    default:
      // Fallback for any unexpected key (shouldn't happen with TypeScript)
      valA = 0;
      valB = 0;
  }

  // Perform comparison based on type
  let comparison = 0;
  if (typeof valA === 'string' && typeof valB === 'string') {
    comparison = valA.localeCompare(valB);
  } else if (typeof valA === 'number' && typeof valB === 'number') {
    // Direct numeric comparison
    comparison = valA - valB;
  }
  // Add more type checks if needed (e.g., for dates)

  return comparison * orderMultiplier;
};