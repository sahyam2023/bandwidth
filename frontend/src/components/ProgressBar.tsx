// src/components/ProgressBar.tsx
import { FC } from 'react';

interface ProgressBarProps {
  value: number;
  large?: boolean; // Keep the prop for height calculation
  small?: boolean; // Keep the prop for height calculation
}

const ProgressBar: FC<ProgressBarProps> = ({ value, large, small }) => {
  const getColorClass = (percent: number): string => {
    // Make sure negative values are handled gracefully (e.g., for N/A)
    const validPercent = Math.max(0, percent);
    if (validPercent >= 90) return 'bg-red-500';
    if (validPercent >= 75) return 'bg-yellow-500';
    // Default to green, maybe add a grey for 0 or N/A if desired
    return 'bg-green-500';
  };

  // Determine height based on props
  const height = small ? 'h-2' : large ? 'h-4' : 'h-3';

  return (
    // Outer container remains the same
    <div className={`w-full bg-gray-200 rounded-full ${height} overflow-hidden`}> {/* Added overflow-hidden */}
      {/* Inner colored bar */}
      <div
        className={`${getColorClass(value)} rounded-full ${height} transition-all duration-300`}
        style={{ width: `${Math.min(Math.max(0, value), 100)}%` }} // Ensure width is between 0 and 100
      >
      </div>
    </div>
  );
};

export default ProgressBar;