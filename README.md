# Bandwidth Monitoring System

## Overview

This project is a full-stack bandwidth and system health monitoring solution. It consists of:

- **Agent (`agent.py`)**: Collects system metrics (CPU, memory, disk, network, peer traffic) from hosts and reports to a central collector.
- **Collector (`simple_ui_collector.py`)**: Flask-based server that receives, stores, and visualizes metrics and alerts. Data is stored in a SQLite database (`collector_data.db`).
- **Frontend (`frontend/`)**: A modern React + TypeScript web UI for real-time and historical visualization of metrics, alerts, network topology, and peer traffic.

## Features

- **Agent**: 
  - Monitors system resources using `psutil` and optionally peer traffic using `scapy`.
  - Periodically reports data to the collector.
  - Configurable reporting intervals and monitored disks.

- **Collector**:
  - REST API for receiving agent data.
  - Stores metrics and alerts in SQLite.
  - Cleans up old data automatically.
  - Configurable alert thresholds for CPU, memory, disk, and agent status.

- **Frontend**:
  - Built with Vite, React, and Tailwind CSS.
  - Pages for Alerts, History, Network Focus, Peer Traffic.
  - Visualizes agent status, alerts, network topology, and historical trends.
  - Uses Chart.js, D3, and other libraries for rich data visualization.

## Getting Started

### Prerequisites

- Python 3.8+
- Node.js 18+
- (Optional) Npcap/WinPcap for peer traffic monitoring on Windows

### Backend Setup

1. Install Python dependencies:
   ```bash
   pip install psutil flask scapy python-dateutil
   ```
2. Start the collector:
   ```bash
   python simple_ui_collector.py
   ```
3. Start agents on target hosts:
   ```bash
   python agent.py
   ```

### Frontend Setup

1. Install dependencies:
   ```bash
   cd frontend
   npm install
   ```
2. Start the development server:
   ```bash
   npm run dev
   ```
   The frontend will proxy API requests to the collector at `localhost:8000`.

## Project Structure

```
agent.py                  # Agent script for metric collection
simple_ui_collector.py    # Flask collector server
collector_data.db         # SQLite database for metrics/history
frontend/                 # React + TypeScript frontend
  src/
    components/           # Reusable UI components
    pages/                # Main application pages
    utils/                # Utility functions
    types.ts              # Shared TypeScript types
templates/                # HTML templates for Flask
```

## Configuration

- **Agent**: Configure collector IP, port, reporting interval, and disks to monitor in `agent.py`.
- **Collector**: Adjust alert thresholds and data retention in `simple_ui_collector.py`.
- **Frontend**: API endpoint is proxied via Vite config (`/api` → `localhost:8000`).

## License

MIT
