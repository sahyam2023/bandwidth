// src/pages/PeerTrafficPage.tsx
import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import ForceGraph2D, { NodeObject, LinkObject } from 'react-force-graph-2d';
import { rgb } from 'd3-color';
import { scaleOrdinal } from 'd3-scale';
import { schemeCategory10 } from 'd3-scale-chromatic';
import { Transition } from '@headlessui/react';
import { useNavigate } from 'react-router-dom';
import { Menu, Item, Separator, useContextMenu, ItemParams } from 'react-contexify';
import 'react-contexify/ReactContexify.css'; // Import default CS
import {
  X, Server, Zap, Loader2, AlertTriangle, Info, BookOpen, ArrowRightLeft, Search, MemoryStick, Cpu, HardDrive, Eye, Pin, PinOff, History
} from 'lucide-react';
import * as d3 from 'd3-force';

// --- Types ---
interface GraphNode extends NodeObject {
  id: string;
  name: string;
  hostname?: string;
  is_collector: boolean;
  x?: number; y?: number;
  vx?: number; vy?: number;
  fx?: number | undefined;
  fy?: number | undefined;
  __bckgDimensions?: number[];
}
interface GraphLink extends LinkObject {
  source: string | GraphNode; target: string | GraphNode;
  rate_mbps: number; type?: 'reporting' | 'peer_traffic';
}
interface GraphData { nodes: GraphNode[]; links: GraphLink[]; }
interface NodeMenuProps {
  node: GraphNode;
  isFixed: boolean;
}

// --- Constants ---
const NODE_SIZE_AGENT = 10;
const NODE_SIZE_COLLECTOR = 15;
const NODE_COLOR_COLLECTOR = 'rgba(16, 185, 129, 0.95)'; // Teal
const NODE_COLOR_AGENT_FALLBACK = 'rgba(110, 110, 130, 0.8)'; // Grayish
const NODE_HOVER_BRIGHTNESS = 0.6;
// --- NEW: Highlighting ---
const NODE_HIGHLIGHT_OPACITY = 1.0;
const NODE_FADE_OPACITY = 0.15; // How much other nodes/links fade
// const LINK_HIGHLIGHT_OPACITY = 1.0;
const LINK_FADE_OPACITY = 0.1;
// Link Style
const LINK_WIDTH_SCALE = 1.5;
const MAX_LINK_WIDTH = 7;
const MIN_LINK_WIDTH = 1.0;
const ARROW_LENGTH = 6;
const ARROW_COLOR = 'rgba(150, 150, 150, 0.9)'; // Gray
const LINK_COLOR_REPORTING = 'rgba(150, 150, 150, 0.4)'; // Light Gray
// const LINK_COLOR_PEER = 'rgba(128, 128, 255, 0.7)'; // Purpleish
// Particle Style
const PARTICLE_MIN_RATE_MBPS = 5;
const PARTICLE_COUNT = 2;
const PARTICLE_WIDTH = 3.0;
const PARTICLE_COLOR = 'rgba(255, 255, 0, 0.8)'; // Yellow
const PARTICLE_SPEED_SCALE = 0.00003;
// *** ADD THESE TWO LINES ***
const PARTICLE_SPEED_MIN = 0.008;
const PARTICLE_SPEED_MAX = 0.06;
// Force Layout
const FORCE_CHARGE_STRENGTH = -200;
const FORCE_LINK_DISTANCE = 80;
const FORCE_CENTER_STRENGTH = 0.03;
const FORCE_ALPHA_DECAY = 0.0228;
const FORCE_VELOCITY_DECAY = 0.4;
// Search
const SEARCH_ZOOM_LEVEL = 2.5;
const SEARCH_TRANSITION_MS = 1000;

const NODE_CONTEXT_MENU_ID = "node-context-menu";
// --- Constants ---
// ... (other constants)

// --- NEW: Link Color Thresholds & Colors ---
const LINK_RATE_THRESHOLD_LOW_MBPS = 1;    // Below this is considered 'low'
const LINK_RATE_THRESHOLD_MEDIUM_MBPS = 10;  // Between low and medium
const LINK_RATE_THRESHOLD_HIGH_MBPS = 50;  // Between medium and high
// Anything above HIGH is considered 'very high'

const LINK_COLOR_LOW = 'rgba(100, 149, 237, 0.6)';   // Cornflower Blue (adjust as desired)
const LINK_COLOR_MEDIUM = 'rgba(255, 165, 0, 0.7)';  // Orange
const LINK_COLOR_HIGH = 'rgba(255, 69, 0, 0.8)';    // OrangeRed
const LINK_COLOR_VERY_HIGH = 'rgba(220, 20, 60, 0.9)'; // Crimson / Red
// Keep existing colors
// const LINK_COLOR_PEER = 'rgba(128, 128, 255, 0.7)'; // Old uniform peer color - replaced




// --- Component ---
function PeerTrafficPage() {
  const [fullGraphData, setFullGraphData] = useState<GraphData>({ nodes: [], links: [] });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [dimensions, setDimensions] = useState<{ width: number; height: number } | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<any>();
  const [selectedNodeData, setSelectedNodeData] = useState<GraphNode | null>(null);
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const [fixedNodeIds, setFixedNodeIds] = useState<Set<string>>(new Set());
  // --- NEW: Search State ---
  const [searchTerm, setSearchTerm] = useState('');
  const [searchResults, setSearchResults] = useState<GraphNode[]>([]);
  const [isSearchFocused, setIsSearchFocused] = useState(false);
  const [latestAgentMetrics, setLatestAgentMetrics] = useState<Record<string, any>>({});
  const { show } = useContextMenu({ id: NODE_CONTEXT_MENU_ID });
  const navigate = useNavigate(); // For "View History"


  // --- Data Fetching ---
  const fetchData = useCallback(async () => {
    if (!loading) setError(null);

    try {
      // Fetch graph data (nodes/links)
      const graphResponse = await fetch('/api/all_peer_flows');
      if (!graphResponse.ok) { throw new Error(`API Error (Graph): ${graphResponse.status}`); }
      const graphApiData = await graphResponse.json();
      if (!graphApiData || !Array.isArray(graphApiData.nodes) || !Array.isArray(graphApiData.links)) {
        throw new Error("Invalid graph data format");
      }

      // Fetch latest metrics data
      const metricsResponse = await fetch('/api/latest_data');
      if (!metricsResponse.ok) { throw new Error(`API Error (Metrics): ${metricsResponse.status}`); }
      const metricsApiData = await metricsResponse.json();
      // Basic check if it's an object (expected format: { hostname: { data: {...}, last_seen: ... } })
      if (typeof metricsApiData !== 'object' || metricsApiData === null) {
        throw new Error("Invalid latest metrics data format");
      }

      // Process graph data (keep existing logic)
      setFullGraphData(prevData => {
        const existingNodesMap = new Map(prevData.nodes.map(n => [n.id, n]));
        const processedNodes: GraphNode[] = graphApiData.nodes.map((n: any) => {
          const nodeId = n.id ?? n.hostname ?? String(Math.random());
          const existingNode = existingNodesMap.get(nodeId);
          return {
            ...(existingNode ?? {}),
            ...n,
            id: nodeId,
            name: n.name ?? n.hostname ?? nodeId ?? 'Unknown',
            is_collector: n.is_collector ?? false,
            fx: fixedNodeIds.has(nodeId) ? existingNode?.fx ?? undefined : undefined,
            fy: fixedNodeIds.has(nodeId) ? existingNode?.fy ?? undefined : undefined,
          };
        });
        const processedLinks: GraphLink[] = graphApiData.links.map((l: any) => ({
          // ... keep link processing logic ...
          ...l,
          source: typeof l.source === 'object' && l.source !== null ? (l.source.id ?? l.source.hostname) : String(l.source),
          target: typeof l.target === 'object' && l.target !== null ? (l.target.id ?? l.target.hostname) : String(l.target),
          rate_mbps: typeof l.rate_mbps === 'number' ? l.rate_mbps : 0,
          type: l.type,
        }));
        return { nodes: processedNodes, links: processedLinks };
      });

      // Store the latest metrics data
      // Extract the 'data' part which contains the actual metrics
      const extractedMetrics: Record<string, any> = {};
      for (const hostname in metricsApiData) {
        if (metricsApiData[hostname] && metricsApiData[hostname].data) {
          extractedMetrics[hostname] = metricsApiData[hostname].data;
        }
      }
      setLatestAgentMetrics(extractedMetrics);


      setLastUpdated(new Date()); setError(null);
    } catch (e) { console.error("Fetch error:", e); setError(e instanceof Error ? e.message : 'Unknown fetch error.'); }
    finally { setLoading(false); }
  }, [loading, fixedNodeIds]); // Dependencies remain the same

  // --- Effects ---
  // Keep existing effects
  useEffect(() => { fetchData(); const interval = setInterval(fetchData, 5000); return () => clearInterval(interval); }, [fetchData]);
  useEffect(() => { const observer = new ResizeObserver(entries => { if (entries && entries[0]) { const { width, height } = entries[0].contentRect; if (width > 0 && height > 0) { setDimensions(currentDims => (!currentDims || width !== currentDims.width || height !== currentDims.height) ? { width, height } : currentDims); } } }); const currentRef = containerRef.current; if (currentRef) { observer.observe(currentRef); } return () => { if (currentRef) { observer.unobserve(currentRef); } observer.disconnect(); }; }, []);
  useEffect(() => {
    if (graphRef.current) {
      const fg = graphRef.current;
      fg.d3Force('charge', d3.forceManyBody().strength(FORCE_CHARGE_STRENGTH));
      fg.d3Force('link')?.distance(FORCE_LINK_DISTANCE).strength(0.6);
      fg.d3Force('center', d3.forceCenter(0, 0).strength(FORCE_CENTER_STRENGTH));
      fg.d3AlphaDecay(FORCE_ALPHA_DECAY);
      fg.d3VelocityDecay(FORCE_VELOCITY_DECAY);
    }
  }, []); // Apply forces once

  // --- Memoized Color Map ---
  // Keep existing agentColorMap
  const agentColorMap = useMemo(() => {
    const agentNodes = fullGraphData.nodes.filter(node => !node.is_collector);
    const colorScale = scaleOrdinal(schemeCategory10);
    const map = new Map<string, string>();
    agentNodes.forEach(agent => {
      const color = colorScale(agent.id as string) as string;
      map.set(agent.id as string, color);
    });
    return map;
  }, [fullGraphData.nodes]);

  // --- Memoized Highlighted Nodes & Neighbors ---
  const highlightedNodes = useMemo(() => {
    const highlight = new Set<string>();
    const activeNodeId = selectedNodeData?.id ?? hoveredNodeId;

    if (activeNodeId) {
      highlight.add(activeNodeId);
      fullGraphData.links.forEach(link => {
        const sourceId = typeof link.source === 'object' && link.source !== null ? link.source.id : link.source as string;
        const targetId = typeof link.target === 'object' && link.target !== null ? link.target.id : link.target as string;
        if (sourceId === activeNodeId) {
          highlight.add(targetId);
        } else if (targetId === activeNodeId) {
          highlight.add(sourceId);
        }
      });
    }
    return highlight;
  }, [selectedNodeData, hoveredNodeId, fullGraphData.links]);


  // --- Calculate Connected Peers for Selected Node (Add Aggregate) ---
  const selectedPeerTraffic = useMemo(() => {
    // Keep existing calculation logic
    if (!selectedNodeData || selectedNodeData.is_collector) return { peers: [], totalIn: 0, totalOut: 0 };

    const nodeId = selectedNodeData.id;
    const relatedPeerLinks = fullGraphData.links.filter(link => {
      if (link.type !== 'peer_traffic') return false;
      const sourceId = typeof link.source === 'object' && link.source !== null ? link.source.id : link.source as string;
      const targetId = typeof link.target === 'object' && link.target !== null ? link.target.id : link.target as string;
      return sourceId === nodeId || targetId === nodeId;
    });

    let totalIn = 0;
    let totalOut = 0;

    const peers = relatedPeerLinks.map(link => {
      const sourceId = typeof link.source === 'object' && link.source !== null ? link.source.id : link.source as string;
      const targetId = typeof link.target === 'object' && link.target !== null ? link.target.id : link.target as string;
      const isOutbound = sourceId === nodeId;
      const peerId = isOutbound ? targetId : sourceId;
      const peerNode = fullGraphData.nodes.find(n => n.id === peerId);
      const peerName = peerNode?.name ?? peerId;
      const rateMbps = link.rate_mbps;

      if (isOutbound) totalOut += rateMbps; else totalIn += rateMbps;

      return {
        key: `${sourceId}-${targetId}-${rateMbps}`,
        direction: isOutbound ? 'outbound' : 'inbound',
        peerDisplay: peerName,
        rateMbps: rateMbps
      };
    }).sort((a, b) => b.rateMbps - a.rateMbps);

    return { peers, totalIn, totalOut };

  }, [selectedNodeData, fullGraphData.links, fullGraphData.nodes]);

  const graphDataWithReportingLinks = useMemo(() => {
    if (!fullGraphData.nodes || fullGraphData.nodes.length === 0) {
      return { nodes: [], links: [] }; // Return empty if no nodes
    }

    // Find the collector node
    const collectorNode = fullGraphData.nodes.find(n => n.is_collector);

    // Start with existing nodes and peer links
    const nodes = [...fullGraphData.nodes];
    const peerLinks = fullGraphData.links.filter(l => l.type !== 'reporting'); // Filter out any potential old reporting links

    const reportingLinks: GraphLink[] = [];

    if (collectorNode) {
      // Add reporting links from each agent *to* the collector
      nodes.forEach(node => {
        if (!node.is_collector && node.id) { // Ensure it's an agent and has an ID
          // Check if a reporting link already exists (less likely now but safe)
          // const exists = fullGraphData.links.some(l => l.type === 'reporting' && l.source === node.id && l.target === collectorNode.id);
          // if (!exists) {
          reportingLinks.push({
            source: node.id, // Agent is the source
            target: collectorNode.id, // Collector is the target
            rate_mbps: 0, // Reporting links have no specific rate here
            type: 'reporting' // Mark link type
          });
          // }
        }
      });
    } else {
      console.warn("Collector node not found in graph data. Cannot draw reporting links.");
    }

    // Combine peer links and reporting links
    return {
      nodes: nodes,
      links: [...peerLinks, ...reportingLinks]
    };
  }, [fullGraphData]); // Recalculate when fullGraphData changes



  // --- Interaction Handlers ---
  const handleNodeClick = useCallback((node: GraphNode | null) => { setSelectedNodeData(node); }, []);
  const handleBackgroundClick = useCallback(() => { setSelectedNodeData(null); setSearchTerm(''); setSearchResults([]); }, []); // Clear search on bg click
  const handleNodeHover = useCallback((node: GraphNode | null) => { setHoveredNodeId(node ? node.id as string : null); }, []);
  const handleNodeDragEnd = useCallback((node: GraphNode) => {
    if (node && node.id) {
      node.fx = node.x;
      node.fy = node.y;
      setFixedNodeIds(prev => new Set(prev).add(node.id as string));
    }
  }, []);
  const handleNodeRightClick = useCallback((node: GraphNode, event: MouseEvent) => {
    event.preventDefault();
    if (!node || !node.id) return;
    const nodeId = node.id as string;
    const isFixed = fixedNodeIds.has(nodeId);
    // Pass strongly typed props
    show({ event, props: { node, isFixed } });
  }, [fixedNodeIds, show]);

  const handleMenuViewDetails = useCallback(({ props }: ItemParams<NodeMenuProps | undefined>) => {
    // Safely access props and node
    if (props?.node) {
      setSelectedNodeData(props.node);
    }
  }, [setSelectedNodeData]);

  const handleMenuViewHistory = useCallback(({ props }: ItemParams<NodeMenuProps | undefined>) => {
    const node = props?.node;
    // --- Use node.hostname for navigation ---
    if (node && node.hostname) { // Check if hostname field exists
      const hostnameToNavigate = node.hostname;
      console.log(`Navigating to history for hostname: ${hostnameToNavigate}`);
      navigate(`/history?hostname=${encodeURIComponent(hostnameToNavigate)}`);
    } else if (node) {
      // Fallback or warning if hostname is missing on the node object
      console.warn(`Node ${node.id} (${node.name}) is missing hostname field for history navigation.`);
      alert(`Cannot navigate to history: Hostname not available for node ${node.name || node.id}.`);
    }
  }, [navigate]);
  const handleMenuToggleFix = useCallback(({ props }: ItemParams<NodeMenuProps | undefined>) => {
    const node = props?.node;
    const isCurrentlyFixed = props?.isFixed;
    const nodeId = node?.id as string | undefined;

    if (!node || !nodeId) return;

    if (isCurrentlyFixed) { // Unfix
      node.fx = undefined; node.fy = undefined; node.vx = 0; node.vy = 0;
      setFixedNodeIds(prev => { const newSet = new Set(prev); newSet.delete(nodeId); return newSet; });
    } else { // Fix
      node.fx = node.x; node.fy = node.y;
      setFixedNodeIds(prev => new Set(prev).add(nodeId));
    }
  }, [setFixedNodeIds]);

  // --- NEW: Search Handlers ---
  const handleSearchChange = useCallback((event: React.ChangeEvent<HTMLInputElement>) => {
    const term = event.target.value;
    setSearchTerm(term);
    if (term.length > 1) {
      const results = fullGraphData.nodes.filter(node =>
        node.name.toLowerCase().includes(term.toLowerCase()) ||
        node.id.toLowerCase().includes(term.toLowerCase()) ||
        node.hostname?.toLowerCase().includes(term.toLowerCase())
      ).slice(0, 10); // Limit results
      setSearchResults(results);
    } else {
      setSearchResults([]);
    }
  }, [fullGraphData.nodes]);

  const handleSearchResultClick = useCallback((node: GraphNode) => {
    if (node && node.x != null && node.y != null && graphRef.current) {
      graphRef.current.centerAt(node.x, node.y, SEARCH_TRANSITION_MS);
      graphRef.current.zoom(SEARCH_ZOOM_LEVEL, SEARCH_TRANSITION_MS);
      setSelectedNodeData(node); // Select the node
    }
    setSearchTerm(''); // Clear search term
    setSearchResults([]); // Clear results
    setIsSearchFocused(false); // Hide dropdown
  }, [graphRef, setSelectedNodeData]);


  // --- NEW: Calculate Summary Stats ---
  const summaryStats = useMemo(() => {
    const totalNodes = fullGraphData.nodes.length;
    const totalAgents = fullGraphData.nodes.filter(n => !n.is_collector).length;
    const totalCollectors = totalNodes - totalAgents;
    const peerLinks = fullGraphData.links.filter(l => l.type === 'peer_traffic');
    const totalPeerLinks = peerLinks.length;
    const aggregatePeerMbps = peerLinks.reduce((sum, link) => sum + (link.rate_mbps || 0), 0);
    return { totalAgents, totalCollectors, totalPeerLinks, aggregatePeerMbps };
  }, [fullGraphData]);

  const nodeMetrics = selectedNodeData
    ? latestAgentMetrics[selectedNodeData.name] // Get metrics using hostname
    : null;



  // --- Render Logic ---
  const renderWidth = dimensions?.width ?? 0;
  const renderHeight = dimensions?.height ?? 0;
  const graphAreaReady = renderWidth > 0 && renderHeight > 0;
  const isDarkMode = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;


  return (
    // --- Added relative positioning for search results dropdown ---
    <div className="relative p-4 sm:p-6 flex flex-col h-full bg-gray-50 dark:bg-gray-900 overflow-hidden">
      {/* Header */}
      <div className="flex justify-between items-center mb-2 flex-shrink-0 px-1 gap-4">
        <h1 className="text-xl sm:text-2xl font-bold text-gray-900 dark:text-gray-100 whitespace-nowrap"> Peer Traffic Graph </h1>

        {/* --- NEW: Search Input --- */}
        <div className="relative flex-grow max-w-xs">
          <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
            <Search className="h-4 w-4 text-gray-400" aria-hidden="true" />
          </div>
          <input
            type="text"
            name="search"
            id="search"
            className="block w-full pl-10 pr-3 py-1.5 text-sm rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 placeholder-gray-400 dark:placeholder-gray-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 focus:border-indigo-500"
            placeholder="Find Node..."
            value={searchTerm}
            onChange={handleSearchChange}
            onFocus={() => setIsSearchFocused(true)}
            onBlur={() => setTimeout(() => setIsSearchFocused(false), 150)} // Delay blur to allow click on results
            aria-label="Search nodes"
          />
          {/* --- NEW: Search Results Dropdown --- */}
          {isSearchFocused && searchResults.length > 0 && (
            <ul className="absolute z-30 mt-1 w-full bg-white dark:bg-gray-700 shadow-lg max-h-60 rounded-md py-1 text-base ring-1 ring-black ring-opacity-5 overflow-auto focus:outline-none sm:text-sm">
              {searchResults.map((node) => (
                <li
                  key={node.id}
                  className="text-gray-900 dark:text-gray-100 cursor-pointer select-none relative py-2 pl-3 pr-9 hover:bg-indigo-600 hover:text-white"
                  onMouseDown={(e) => { // Use onMouseDown to prevent blur before click
                    e.preventDefault();
                    handleSearchResultClick(node);
                  }}
                >
                  <span className="block truncate">{node.name}</span>
                  <span className="block truncate text-xs text-gray-500 dark:text-gray-400 ml-2">{node.id}</span>
                </li>
              ))}
            </ul>
          )}
        </div>


        <div className='text-xs text-gray-500 dark:text-gray-400 whitespace-nowrap text-right'> {lastUpdated ? `Last updated: ${lastUpdated.toLocaleTimeString()}` : ''} </div>
      </div>

      {/* --- NEW: Summary Stats Bar --- */}
      <div className="mb-2 px-1 text-xs text-gray-600 dark:text-gray-400 flex flex-wrap gap-x-4 gap-y-1 border-b dark:border-gray-700 pb-1">
        <span>Agents: <strong className="text-gray-800 dark:text-gray-200">{summaryStats.totalAgents}</strong></span>
        <span>Collectors: <strong className="text-gray-800 dark:text-gray-200">{summaryStats.totalCollectors}</strong></span>
        <span>Peer Links: <strong className="text-gray-800 dark:text-gray-200">{summaryStats.totalPeerLinks}</strong></span>
        <span>Detected Peer Rate: <strong className="text-gray-800 dark:text-gray-200">{summaryStats.aggregatePeerMbps.toFixed(1)} Mbps*</strong></span>
        <span className="text-gray-400 dark:text-gray-500 ml-auto md:ml-2 text-[10px] italic">*Peer rates may be underestimated under high load.</span>
      </div>


      {/* Loading / Error / Empty States --- UPDATED --- */}
      {loading && !error && (
        <div className="flex-grow flex items-center justify-center text-gray-500 dark:text-gray-400">
          <Loader2 size={24} className="animate-spin mr-2" />Loading graph data...
        </div>
      )}
      {error && (
        <div className="flex-grow flex items-center justify-center p-4 text-red-600 dark:text-red-400">
          <AlertTriangle size={24} className="mr-2" /> Error: {error}
        </div>
      )}
      {!loading && !error && fullGraphData.nodes.length === 0 && (
        <div className="flex-grow flex items-center justify-center p-4 text-gray-500 dark:text-gray-400 italic">
          <Info size={20} className="mr-2" /> No graph data available.
        </div>
      )}

      {/* Graph Container */}
      <div ref={containerRef} className={` flex-grow border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 shadow relative overflow-hidden ${graphAreaReady && !loading && !error && fullGraphData.nodes.length > 0 ? 'opacity-100' : 'opacity-0'} `} style={{ minHeight: '300px', transition: 'opacity 0.3s ease-in-out' }} >
        {graphAreaReady && !loading && !error && graphDataWithReportingLinks.nodes.length > 0 && (
          <ForceGraph2D<GraphNode, GraphLink>
            ref={graphRef}
            graphData={graphDataWithReportingLinks}
            width={renderWidth}
            height={renderHeight}
            cooldownTicks={100}
            enableZoomInteraction={true}
            enablePointerInteraction={true}
            onNodeDragEnd={handleNodeDragEnd}
            onNodeClick={handleNodeClick}
            onNodeRightClick={handleNodeRightClick}
            onBackgroundClick={handleBackgroundClick}
            onNodeHover={handleNodeHover}
            nodeRelSize={1}
            linkHoverPrecision={8}

            // Nodes
            nodeVal={1}
            nodeCanvasObject={(node: GraphNode, ctx: CanvasRenderingContext2D, globalScale: number) => {
              const nodeId = node.id as string;
              const isCollector = node.is_collector;
              const baseRadius = (isCollector ? NODE_SIZE_COLLECTOR : NODE_SIZE_AGENT) / 2;
              let nodeColor = isCollector ? NODE_COLOR_COLLECTOR : (agentColorMap.get(nodeId) || NODE_COLOR_AGENT_FALLBACK);
              const isFixed = fixedNodeIds.has(nodeId);
              const isHovered = nodeId === hoveredNodeId;
              const isSelected = nodeId === selectedNodeData?.id;
              let radius = baseRadius * (isSelected || isHovered ? 1.3 : 1.0);

              // --- Highlighting Logic ---
              const isHighlighted = highlightedNodes.has(nodeId);
              const fade = highlightedNodes.size > 0 && !isHighlighted;
              ctx.save(); // Save context state before potentially changing alpha
              ctx.globalAlpha = fade ? NODE_FADE_OPACITY : NODE_HIGHLIGHT_OPACITY;

              // Apply hover brightness effect (only if not fading)
              if (!fade && isHovered && !isCollector) {
                try { nodeColor = rgb(nodeColor).brighter(NODE_HOVER_BRIGHTNESS).toString(); } catch (e) { }
              }

              // Draw the node circle
              ctx.fillStyle = nodeColor;
              ctx.beginPath();
              ctx.arc(node.x ?? 0, node.y ?? 0, radius, 0, 2 * Math.PI, false);
              ctx.fill();

              // Draw the fixed indicator (always opaque for visibility)
              if (isFixed) {
                ctx.fillStyle = fade ? 'rgba(0, 0, 0, 0.2)' : 'rgba(0, 0, 0, 0.6)'; // Adjust fixed indicator fade
                ctx.beginPath();
                ctx.arc(node.x ?? 0, node.y ?? 0, radius * 0.3, 0, 2 * Math.PI, false);
                ctx.fill();
              }

              // --- Node Label Drawing ---
              const label = node.name || nodeId || "UNKNOWN";
              const nodeX = node.x ?? 0;
              const nodeY = node.y ?? 0;
              const labelYOffset = 4;
              const fontSize = Math.max(3.5, 9 / (globalScale ** 0.6));

              // Only draw label if not faded or if selected/hovered
              if (!fade || isSelected || isHovered) {
                ctx.font = `${fontSize}px Sans-Serif`;
                ctx.textAlign = 'center';
                ctx.textBaseline = 'bottom';

                let labelColor = isDarkMode ? 'white' : 'black';
                try {
                  const parsed = rgb(nodeColor);
                  const luminance = (0.299 * parsed.r + 0.587 * parsed.g + 0.114 * parsed.b) / 255;
                  if (isDarkMode) { labelColor = (luminance < 0.55) ? 'white' : 'black'; }
                  else { labelColor = '#333'; }
                } catch (e) { /* Use default */ }

                ctx.fillStyle = labelColor;
                const labelY = nodeY - radius - labelYOffset;
                ctx.fillText(label, nodeX, labelY);
              }

              ctx.restore(); // Restore context state (clears globalAlpha)
            }}
            nodePointerAreaPaint={(node: GraphNode, color: string, ctx: CanvasRenderingContext2D) => {
              const baseRadius = node.is_collector ? NODE_SIZE_COLLECTOR : NODE_SIZE_AGENT;
              const pointerRadius = baseRadius * 1.2;
              ctx.fillStyle = color;
              ctx.beginPath();
              ctx.arc(node.x ?? 0, node.y ?? 0, pointerRadius, 0, 2 * Math.PI, false);
              ctx.fill();
            }}

            // Links
            linkWidth={(link: GraphLink) => {
              // Make reporting links thinner than peer links
              if (link.type === 'reporting') return MIN_LINK_WIDTH * 0.6;
              return Math.min(MAX_LINK_WIDTH, MIN_LINK_WIDTH + (link.rate_mbps || 0) * LINK_WIDTH_SCALE);
            }}
            // --- Link Color for highlighting AND rate ---
            linkColor={(link: GraphLink) => {
              const sourceId = typeof link.source === 'object' ? (link.source as GraphNode)?.id : link.source as string;
              const targetId = typeof link.target === 'object' ? (link.target as GraphNode)?.id : link.target as string;
              const isHighlighted = highlightedNodes.size > 0 && highlightedNodes.has(sourceId) && highlightedNodes.has(targetId);
              const fade = highlightedNodes.size > 0 && !isHighlighted;

              let baseColor: string; // Determine base color based on type and rate

              if (link.type === 'peer_traffic') {
                const rate = link.rate_mbps || 0;
                if (rate >= LINK_RATE_THRESHOLD_HIGH_MBPS) {
                  baseColor = LINK_COLOR_VERY_HIGH;
                } else if (rate >= LINK_RATE_THRESHOLD_MEDIUM_MBPS) {
                  baseColor = LINK_COLOR_HIGH;
                } else if (rate >= LINK_RATE_THRESHOLD_LOW_MBPS) {
                  baseColor = LINK_COLOR_MEDIUM;
                } else {
                  baseColor = LINK_COLOR_LOW;
                }
              } else {
                // Default/Reporting link color
                baseColor = LINK_COLOR_REPORTING;
              }

              // Apply fade effect if needed
              if (!fade) {
                return baseColor; // Return full color if not fading
              } else {
                // Return faded color
                try {
                  const color = rgb(baseColor);
                  // Use LINK_FADE_OPACITY for consistent fading
                  color.opacity = LINK_FADE_OPACITY;
                  return color.toString();
                } catch (e) {
                  // Fallback faded color if parsing fails
                  return `rgba(128, 128, 128, ${LINK_FADE_OPACITY})`;
                }
              }
            }}
            // --- END Link Color ---
            linkLabel={(link: GraphLink) => {
              // Keep existing link label logic
              const sourceId = typeof link.source === 'object' && link.source !== null ? (link.source as GraphNode)?.id : link.source as string;
              const targetId = typeof link.target === 'object' && link.target !== null ? (link.target as GraphNode)?.id : link.target as string;
              const sourceNode = fullGraphData.nodes.find(n => n.id === sourceId);
              const targetNode = fullGraphData.nodes.find(n => n.id === targetId);
              const sourceName = sourceNode?.name ?? sourceId ?? '?';
              const targetName = targetNode?.name ?? targetId ?? '?';
              const rateString = link.type === 'peer_traffic'
                ? `Detected Rate: <strong>${link.rate_mbps?.toFixed(2) ?? 'N/A'} Mbps</strong>*` // Added asterisk/note
                : 'Reporting Link';
              return `
                            <div class="bg-gray-800 text-white p-2 rounded shadow-lg text-xs" style="font-family: sans-serif; max-width: 250px;">
                               From: ${sourceName}<br/>
                               To: ${targetName}<br/>
                               ${rateString}
                               ${link.type === 'peer_traffic' ? '<br/><em style="color:#ccc; font-size: 0.9em;">*May be lower than actual under high load.</em>' : ''} {/* Added note */}
                            </div>
                           `;
            }}
            linkDirectionalArrowLength={(link: GraphLink) => link.type === 'reporting' ? 0 : ARROW_LENGTH}
            // --- Arrow Color for highlighting ---
            linkDirectionalArrowColor={(link: GraphLink) => {
              const sourceId = typeof link.source === 'object' ? (link.source as GraphNode)?.id : link.source as string;
              const targetId = typeof link.target === 'object' ? (link.target as GraphNode)?.id : link.target as string;
              const isHighlighted = highlightedNodes.size > 0 && highlightedNodes.has(sourceId) && highlightedNodes.has(targetId);
              const fade = highlightedNodes.size > 0 && !isHighlighted;
              try {
                const color = rgb(ARROW_COLOR);
                color.opacity = fade ? LINK_FADE_OPACITY : 0.9;
                return color.toString();
              } catch (e) {
                return fade ? `rgba(150, 150, 150, ${LINK_FADE_OPACITY})` : ARROW_COLOR;
              }
            }}
            linkDirectionalArrowRelPos={1}
            linkCurvature={0.1}

            // Particles
            linkDirectionalParticles={(link: GraphLink) =>
              (link.type !== 'reporting' && (link.rate_mbps || 0) >= PARTICLE_MIN_RATE_MBPS) ? PARTICLE_COUNT : 0
            }
            linkDirectionalParticleWidth={PARTICLE_WIDTH}
            // --- Particle Color for highlighting ---
            linkDirectionalParticleColor={(link: GraphLink) => {
              const sourceId = typeof link.source === 'object' ? (link.source as GraphNode)?.id : link.source as string;
              const targetId = typeof link.target === 'object' ? (link.target as GraphNode)?.id : link.target as string;
              const isHighlighted = highlightedNodes.size > 0 && highlightedNodes.has(sourceId) && highlightedNodes.has(targetId);
              const fade = highlightedNodes.size > 0 && !isHighlighted;
              try {
                const color = rgb(PARTICLE_COLOR);
                color.opacity = fade ? NODE_FADE_OPACITY : 0.8; // Fade particles with nodes
                return color.toString();
              } catch (e) {
                return fade ? `rgba(255, 255, 0, ${NODE_FADE_OPACITY})` : PARTICLE_COLOR;
              }
            }}
            linkDirectionalParticleSpeed={(link: GraphLink) =>
              link.type !== 'reporting'
                ? Math.max(PARTICLE_SPEED_MIN, Math.min(PARTICLE_SPEED_MAX, (link.rate_mbps || 0) * PARTICLE_SPEED_SCALE))
                : 0
            }
          />
        )}

        {/* Info Side Panel --- CORRECTED --- */}
        <Transition
          show={selectedNodeData !== null}
          enter="transition-transform duration-300 ease-out"
          enterFrom="translate-x-full" enterTo="translate-x-0"
          leave="transition-transform duration-200 ease-in"
          leaveFrom="translate-x-0" leaveTo="translate-x-full"
        >
          <div className="absolute top-0 right-0 h-full z-20 pointer-events-none">
            {/* Added overflow-hidden here to contain content */}
            <div className="h-full w-64 sm:w-72 bg-white dark:bg-gray-800 shadow-lg pointer-events-auto flex flex-col border-l border-gray-200 dark:border-gray-700 overflow-hidden">
              {/* Panel Header */}
              <div className="p-3 border-b border-gray-200 dark:border-gray-700 flex justify-between items-center flex-shrink-0"> {/* Reduced padding */}
                <h2 className="font-semibold text-lg text-gray-900 dark:text-gray-100 flex items-center gap-2">
                  {selectedNodeData?.is_collector ? <Zap size={18} className="text-teal-500" /> : <Server size={18} className="text-blue-500" />} Node Info
                </h2>
                <button onClick={() => setSelectedNodeData(null)} className="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-600 text-gray-500 dark:text-gray-400" title="Close Panel"> <X size={20} /> </button>
              </div>

              {/* Panel Content */}
              <div className="p-3 overflow-y-auto flex-grow text-sm space-y-3"> {/* Reduced padding/space */}
                {selectedNodeData ? (
                  <>
                    {/* Basic Info Section (uses selectedNodeData) */}
                    <div className="space-y-1 border-b border-gray-200 dark:border-gray-700 pb-2 mb-2"> {/* Reduced padding/margins */}
                      <div><strong className="text-gray-600 dark:text-gray-400 w-20 inline-block">Name:</strong> <span className="font-medium text-gray-900 dark:text-gray-100 break-words">{selectedNodeData.name}</span></div>
                      <div><strong className="text-gray-600 dark:text-gray-400 w-20 inline-block">ID:</strong> <code className="text-xs text-gray-700 dark:text-gray-300 break-all">{selectedNodeData.id}</code></div>
                      <div><strong className="text-gray-600 dark:text-gray-400 w-20 inline-block">Type:</strong> {selectedNodeData.is_collector ? <span className="font-semibold text-teal-600 dark:text-teal-400">Collector</span> : <span className="text-blue-600 dark:text-blue-400">Agent</span>}</div>
                      {selectedNodeData.hostname && selectedNodeData.hostname !== selectedNodeData.name && (
                        <div><strong className="text-gray-600 dark:text-gray-400 w-20 inline-block">Hostname:</strong> <span className="text-gray-700 dark:text-gray-300 break-words">{selectedNodeData.hostname}</span></div>
                      )}
                    </div>

                    {/* Fixed Position Info (uses selectedNodeData.id) */}
                    {fixedNodeIds.has(selectedNodeData.id) && (
                      <div className="p-1.5 bg-yellow-100 dark:bg-yellow-900/30 border border-yellow-300 dark:border-yellow-700 rounded text-yellow-800 dark:text-yellow-300 text-xs">
                        Position fixed. Right-click node to unfix.
                      </div>
                    )}

                    {/* Peer Traffic Section (Only for Agents - uses selectedPeerTraffic derived from selectedNodeData) */}
                    {!selectedNodeData.is_collector && (
                      <div className="border-t border-gray-200 dark:border-gray-700 pt-2 mt-2"> {/* Reduced padding/margin */}
                        <h3 className="font-semibold text-gray-800 dark:text-gray-200 mb-1.5 flex items-center gap-1.5">
                          <ArrowRightLeft size={16} /> Detected Peer Rates:
                        </h3>
                        {/* Aggregate Stats */}
                        {selectedPeerTraffic.peers.length > 0 && (
                          <div className="text-xs text-gray-600 dark:text-gray-400 mb-2 border-b dark:border-gray-600 pb-1.5">
                            Total Out: <strong className="text-red-600 dark:text-red-400">{selectedPeerTraffic.totalOut.toFixed(2)} Mbps</strong> /
                            In: <strong className="text-green-600 dark:text-green-400">{selectedPeerTraffic.totalIn.toFixed(2)} Mbps</strong>
                          </div>
                        )}
                        {/* Peer List */}
                        {selectedPeerTraffic.peers.length > 0 ? (
                          <ul className="space-y-1 text-xs max-h-48 overflow-y-auto pr-1">
                            {selectedPeerTraffic.peers.map(({ key, direction, peerDisplay, rateMbps }) => (
                              <li key={key} className="flex justify-between items-center odd:bg-gray-50 dark:odd:bg-gray-700/50 px-1.5 py-0.5 rounded-sm">
                                <span className="flex items-center gap-1 truncate mr-2">
                                  {direction === 'outbound' ? <span title="Outbound" className="text-red-500 font-bold flex-shrink-0 text-base leading-none">→</span> : <span title="Inbound" className="text-green-500 font-bold flex-shrink-0 text-base leading-none">←</span>}
                                  <span className="text-gray-700 dark:text-gray-300 truncate" title={peerDisplay}>{peerDisplay}</span>
                                </span>
                                <span className="font-mono text-blue-600 dark:text-blue-400 whitespace-nowrap">{rateMbps.toFixed(2)} Mbps</span>
                              </li>
                            ))}
                          </ul>
                        ) : (
                          <p className="text-gray-500 dark:text-gray-500 italic text-xs">No active peer traffic detected.</p>
                        )}
                        <p className="text-gray-400 dark:text-gray-500 text-[10px] italic mt-1.5">*Rates may be underestimated under high load.</p>
                      </div>
                    )}

                    {/* Check if nodeMetrics has data and if it's an agent */}
                    {nodeMetrics && !selectedNodeData.is_collector && (
                      <>
                        {/* CPU Usage Section */}
                        <div className="border-t border-gray-200 dark:border-gray-700 pt-2 mt-2">
                          <h3 className="font-semibold text-gray-800 dark:text-gray-200 mb-1 flex items-center gap-1.5"><Cpu size={16} /> CPU Usage:</h3>
                          <p className="text-sm pl-2">{nodeMetrics.cpu_percent != null && nodeMetrics.cpu_percent >= 0
                            ? `${nodeMetrics.cpu_percent.toFixed(1)} %`
                            : <span className="text-gray-400 italic">N/A</span>
                          }</p>
                          {/* Optional: Add a simple bar */}
                          {nodeMetrics.cpu_percent != null && nodeMetrics.cpu_percent >= 0 && (
                            <div className="w-full bg-gray-200 dark:bg-gray-600 rounded h-1.5 mt-1">
                              <div className="bg-blue-500 h-1.5 rounded" style={{ width: `${Math.min(100, nodeMetrics.cpu_percent)}%` }}></div>
                            </div>
                          )}
                        </div>

                        {/* Memory Usage Section */}
                        <div className="border-t border-gray-200 dark:border-gray-700 pt-2 mt-2">
                          <h3 className="font-semibold text-gray-800 dark:text-gray-200 mb-1 flex items-center gap-1.5"><MemoryStick size={16} /> Memory Usage:</h3>
                          <p className="text-sm pl-2">{nodeMetrics.mem_percent != null && nodeMetrics.mem_percent >= 0
                            ? `${nodeMetrics.mem_percent.toFixed(1)} %`
                            : <span className="text-gray-400 italic">N/A</span>
                          }</p>
                          {/* Optional: Add a simple bar */}
                          {nodeMetrics.mem_percent != null && nodeMetrics.mem_percent >= 0 && (
                            <div className="w-full bg-gray-200 dark:bg-gray-600 rounded h-1.5 mt-1">
                              <div className="bg-green-500 h-1.5 rounded" style={{ width: `${Math.min(100, nodeMetrics.mem_percent)}%` }}></div>
                            </div>
                          )}
                        </div>

                        {/* Disk I/O Section */}
                        {nodeMetrics.disk_io && Object.keys(nodeMetrics.disk_io).length > 0 && (
                          <div className="border-t border-gray-200 dark:border-gray-700 pt-2 mt-2">
                            <h3 className="font-semibold text-gray-800 dark:text-gray-200 mb-1.5 flex items-center gap-1.5"><HardDrive size={16} /> Disk I/O:</h3>
                            <div className="space-y-1 text-xs pl-2">
                              {Object.entries(nodeMetrics.disk_io).map(([disk, io]: [string, any]) => (
                                <div key={disk}>
                                  <strong className="text-gray-600 dark:text-gray-400">{disk}:</strong>
                                  {/* Display Read Stats */}
                                  <span className="ml-2">
                                    R: {io.read_ops_ps != null ? io.read_ops_ps.toFixed(1) : '-'} IOPS /
                                    {io.read_Bps != null ? (io.read_Bps / 1024 ** 2).toFixed(1) : '-'} MBs
                                  </span>
                                  {/* Display Write Stats */}
                                  <span className="ml-2">
                                    W: {io.write_ops_ps != null ? io.write_ops_ps.toFixed(1) : '-'} IOPS /
                                    {io.write_Bps != null ? (io.write_Bps / 1024 ** 2).toFixed(1) : '-'} MBs
                                  </span>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}
                      </>
                    )}
                    {/* End of stats section */}

                  </>
                ) : (
                  // Message shown when no node is selected
                  <p className="text-gray-500 italic">Click a node to see details.</p>
                )}
              </div> {/* End Panel Content */}
            </div>
          </div>
        </Transition>

        <div className="absolute bottom-4 left-4 bg-gray-100 dark:bg-gray-800 bg-opacity-90 dark:bg-opacity-80 border border-gray-300 dark:border-gray-600 text-gray-800 dark:text-gray-200 text-xs p-2 rounded-md shadow-lg pointer-events-none z-30 max-w-[180px]">
          <h4 className="font-bold mb-1.5 border-b border-gray-400 dark:border-gray-500 pb-1 flex items-center gap-1"><BookOpen size={14} /> Legend</h4>
          <div className="space-y-1">
            <div className="flex items-center">
              <div style={{ backgroundColor: NODE_COLOR_COLLECTOR }} className="w-3 h-3 rounded-full mr-1.5 flex-shrink-0 border border-black/20"></div>
              <span>Collector</span>
            </div>
            <div className="flex items-center">
              <div style={{ backgroundColor: schemeCategory10[0] }} className="w-3 h-3 rounded-full mr-1.5 flex-shrink-0 border border-black/20"></div> {/* Example agent color */}
              <span>Agent</span>
            </div>
            <div className="flex items-center">
              <div style={{ backgroundColor: LINK_COLOR_REPORTING }} className="w-4 h-0.5 mr-1.5 flex-shrink-0"></div>
              <span>Reporting Link</span>
            </div>
            {/* Peer Traffic Rate Legend */}
            <div className="flex items-center">
              <div style={{ backgroundColor: LINK_COLOR_LOW }} className="w-4 h-1 mr-1.5 flex-shrink-0"></div>
              <span>Peer ( {LINK_RATE_THRESHOLD_LOW_MBPS} Mbps)</span>
            </div>
            <div className="flex items-center">
              <div style={{ backgroundColor: LINK_COLOR_MEDIUM }} className="w-4 h-1 mr-1.5 flex-shrink-0"></div>
              <span>Peer ({LINK_RATE_THRESHOLD_LOW_MBPS}-{LINK_RATE_THRESHOLD_MEDIUM_MBPS} Mbps)</span>
            </div>
            <div className="flex items-center">
              <div style={{ backgroundColor: LINK_COLOR_HIGH }} className="w-4 h-1 mr-1.5 flex-shrink-0"></div>
              <span>Peer ({LINK_RATE_THRESHOLD_MEDIUM_MBPS}-{LINK_RATE_THRESHOLD_HIGH_MBPS} Mbps)</span>
            </div>
            <div className="flex items-center">
              <div style={{ backgroundColor: LINK_COLOR_VERY_HIGH }} className="w-4 h-1 mr-1.5 flex-shrink-0"></div>
              <span>Peer (≥ {LINK_RATE_THRESHOLD_HIGH_MBPS} Mbps)</span>
            </div>
            <div className="flex items-center">
              <div style={{ backgroundColor: PARTICLE_COLOR }} className="w-1.5 h-1.5 rounded-full mr-1.5 flex-shrink-0 animate-pulse"></div>
              <span>Traffic Particles</span>
            </div>
            <div className="flex items-center">
              <div className="w-3 h-3 relative mr-1.5 flex-shrink-0">
                <div className="w-full h-full rounded-full bg-gray-400"></div>
                <div className="absolute inset-0 flex items-center justify-center">
                  <div className="w-1 h-1 bg-black rounded-full"></div>
                </div>
              </div>
              <span>Fixed Position</span>
            </div>
            <p className="text-gray-400 dark:text-gray-500 text-[9px] italic pt-1 border-t border-gray-300 dark:border-gray-600 mt-1">
              *Peer rates estimated via sampling, may be lower than actual under high load.
            </p>
          </div>
        </div>


      </div> {/* End Graph Container */}
      <Menu id={NODE_CONTEXT_MENU_ID} theme="dark" animation="fade">
        {/* ... Menu Header (Optional) ... */}

        <Item onClick={handleMenuViewDetails}>
          <Eye size={14} className="mr-2" /> View Details
        </Item>

        {/* Use correct type for hidden prop function */}
        <Item
          onClick={handleMenuViewHistory}
          // --- MODIFIED TYPE ANNOTATION for hidden ---
          hidden={({ props }: { props?: NodeMenuProps }) => !!props?.node?.is_collector}
        >
          <History size={14} className="mr-2" /> View History
        </Item>

        <Separator />

        {/* Item for "Fix Position" */}
        <Item
          onClick={handleMenuToggleFix}
          // --- MODIFIED TYPE ANNOTATION for hidden ---
          hidden={({ props }: { props?: NodeMenuProps }) => !!props?.isFixed}
        >
          <Pin size={14} className="mr-2 text-green-400" /> Fix Position
        </Item>

        {/* Item for "Unfix Position" */}
        <Item
          onClick={handleMenuToggleFix}
          // --- MODIFIED TYPE ANNOTATION for hidden ---
          hidden={({ props }: { props?: NodeMenuProps }) => !props?.isFixed}
        >
          <PinOff size={14} className="mr-2 text-red-400" /> Unfix Position
        </Item>
      </Menu>
    </div> // End Page Container
  );
}

export default PeerTrafficPage;