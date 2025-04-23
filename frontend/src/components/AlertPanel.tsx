// src/components/AlertPanel.tsx (New File)
import { X, AlertTriangle } from 'lucide-react';
import { AlertInfo } from '../types';

interface AlertPanelProps {
    alerts: AlertInfo[];
    dismissAlert: (alertId: string) => void;
}

function AlertPanel({ alerts, dismissAlert }: AlertPanelProps) {
    if (alerts.length === 0) {
        return null; // Don't render anything if no alerts
    }

    return (
        <div className="fixed bottom-4 right-4 w-80 max-h-60 overflow-y-auto bg-white dark:bg-gray-800 shadow-lg rounded-lg border border-gray-200 dark:border-gray-700 z-50 p-3 space-y-2">
            <h4 className="text-sm font-semibold text-gray-800 dark:text-gray-100 mb-2 border-b dark:border-gray-600 pb-1">Active Alerts</h4>
            {alerts.map((alert) => (
                <div key={alert.id} className="flex items-start gap-2 p-2 rounded bg-yellow-50 dark:bg-yellow-900/30 border border-yellow-200 dark:border-yellow-800/50 text-xs">
                    <AlertTriangle className="w-4 h-4 text-yellow-500 dark:text-yellow-400 mt-0.5 flex-shrink-0" />
                    <div className="flex-grow text-yellow-800 dark:text-yellow-200">
                        {alert.message}
                        <span className="block text-gray-500 dark:text-gray-400 text-[10px] mt-1">
                            {new Date(alert.timestamp).toLocaleTimeString()}
                        </span>
                    </div>
                    <button
                        onClick={() => dismissAlert(alert.id)}
                        className="p-0.5 text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
                        aria-label="Dismiss alert"
                    >
                        <X className="w-3 h-3" />
                    </button>
                </div>
            ))}
        </div>
    );
}

export default AlertPanel;