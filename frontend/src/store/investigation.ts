import { create } from 'zustand';
import type { GraphEdge, GraphNode, GraphPayload, LiveEvent, Stage } from '@/api/types';

export interface GraphFilters {
  minConfidence: number;
  entityTypes: Set<string>;
  edgeTypes: Set<string>;
  hiddenNodes: Set<string>;
  collapsed: Set<string>;
  search: string;
  /** Upper bound of the timeline slider, as an ISO timestamp. */
  until: string | null;
  showCorrelations: boolean;
}

export interface ScanActivity {
  provider: string;
  status: 'running' | 'done' | 'failed' | 'skipped';
  observations: number;
  message?: string;
}

interface InvestigationState {
  graph: GraphPayload | null;
  filters: GraphFilters;
  selectedNodeId: string | null;
  selectedEdgeId: string | null;
  stage: Stage;
  live: boolean;
  activity: Record<string, ScanActivity>;
  errors: string[];

  setGraph: (graph: GraphPayload) => void;
  applyEvent: (event: LiveEvent) => void;
  selectNode: (id: string | null) => void;
  selectEdge: (id: string | null) => void;
  setFilter: <K extends keyof GraphFilters>(key: K, value: GraphFilters[K]) => void;
  toggleEntityType: (type: string) => void;
  toggleHidden: (id: string) => void;
  toggleCollapsed: (id: string) => void;
  resetFilters: () => void;
  setLive: (live: boolean) => void;
  clearActivity: () => void;
}

const defaultFilters = (): GraphFilters => ({
  minConfidence: 0,
  entityTypes: new Set<string>(),
  edgeTypes: new Set<string>(),
  hiddenNodes: new Set<string>(),
  collapsed: new Set<string>(),
  search: '',
  until: null,
  showCorrelations: true,
});

function upsertNode(graph: GraphPayload, node: GraphNode): GraphPayload {
  if (graph.nodes.some((n) => n.id === node.id)) {
    return { ...graph, nodes: graph.nodes.map((n) => (n.id === node.id ? { ...n, ...node } : n)) };
  }
  return { ...graph, nodes: [...graph.nodes, node] };
}

function upsertEdge(graph: GraphPayload, edge: GraphEdge): GraphPayload {
  if (graph.edges.some((e) => e.id === edge.id)) {
    return { ...graph, edges: graph.edges.map((e) => (e.id === edge.id ? { ...e, ...edge } : e)) };
  }
  return { ...graph, edges: [...graph.edges, edge] };
}

export const useInvestigationStore = create<InvestigationState>((set) => ({
  graph: null,
  filters: defaultFilters(),
  selectedNodeId: null,
  selectedEdgeId: null,
  stage: 'idle',
  live: false,
  activity: {},
  errors: [],

  setGraph: (graph) => set({ graph }),

  applyEvent: (event) =>
    set((state) => {
      switch (event.event) {
        case 'entity_discovered':
          // The node is added the moment it is found; no waiting for the scan to finish.
          return state.graph ? { graph: upsertNode(state.graph, event.node) } : {};
        case 'relationship_discovered':
          return state.graph ? { graph: upsertEdge(state.graph, event.edge) } : {};
        case 'stage_changed':
          return { stage: event.stage };
        case 'job_started':
          return {
            activity: {
              ...state.activity,
              [event.provider]: { provider: event.provider, status: 'running', observations: 0 },
            },
          };
        case 'job_finished':
          return {
            activity: {
              ...state.activity,
              [event.provider]: {
                provider: event.provider,
                status: event.error ? 'failed' : 'done',
                observations: event.observations,
                message: event.error ?? undefined,
              },
            },
          };
        case 'provider_skipped':
          return {
            activity: {
              ...state.activity,
              [event.provider]: {
                provider: event.provider,
                status: 'skipped',
                observations: 0,
                message: event.reason,
              },
            },
          };
        case 'error':
          return { errors: [...state.errors.slice(-19), `${event.provider}: ${event.message}`] };
        case 'investigation_completed':
          return {
            stage: 'done',
            graph: state.graph ? { ...state.graph, stats: event.stats } : state.graph,
          };
        default:
          return {};
      }
    }),

  selectNode: (id) => set({ selectedNodeId: id, selectedEdgeId: null }),
  selectEdge: (id) => set({ selectedEdgeId: id, selectedNodeId: null }),

  setFilter: (key, value) => set((state) => ({ filters: { ...state.filters, [key]: value } })),

  toggleEntityType: (type) =>
    set((state) => {
      const next = new Set(state.filters.entityTypes);
      next.has(type) ? next.delete(type) : next.add(type);
      return { filters: { ...state.filters, entityTypes: next } };
    }),

  toggleHidden: (id) =>
    set((state) => {
      const next = new Set(state.filters.hiddenNodes);
      next.has(id) ? next.delete(id) : next.add(id);
      return { filters: { ...state.filters, hiddenNodes: next } };
    }),

  toggleCollapsed: (id) =>
    set((state) => {
      const next = new Set(state.filters.collapsed);
      next.has(id) ? next.delete(id) : next.add(id);
      return { filters: { ...state.filters, collapsed: next } };
    }),

  resetFilters: () => set({ filters: defaultFilters() }),
  setLive: (live) => set({ live }),
  clearActivity: () => set({ activity: {}, errors: [] }),
}));

/** Pure filter pass, shared by the graph canvas and its tests. */
export function visibleGraph(graph: GraphPayload, filters: GraphFilters): GraphPayload {
  const needle = filters.search.trim().toLowerCase();
  const until = filters.until ? Date.parse(filters.until) : null;

  const nodes = graph.nodes.filter((node) => {
    if (filters.hiddenNodes.has(node.id)) return false;
    if (node.confidence < filters.minConfidence) return false;
    if (filters.entityTypes.size > 0 && !filters.entityTypes.has(node.type)) return false;
    if (until && node.first_seen && Date.parse(node.first_seen) > until) return false;
    if (needle && !node.label.toLowerCase().includes(needle)) return false;
    return true;
  });

  // A collapsed node keeps itself but hides the leaves that hang off it.
  const collapsedChildren = new Set<string>();
  for (const id of filters.collapsed) {
    for (const edge of graph.edges) {
      if (edge.source === id) collapsedChildren.add(edge.target);
    }
  }
  const kept = new Set(
    nodes.filter((n) => !collapsedChildren.has(n.id) || filters.collapsed.has(n.id)).map((n) => n.id),
  );

  const edges = graph.edges.filter((edge) => {
    if (!kept.has(edge.source) || !kept.has(edge.target)) return false;
    if (edge.confidence < filters.minConfidence) return false;
    if (!filters.showCorrelations && edge.is_correlation) return false;
    if (filters.edgeTypes.size > 0 && !filters.edgeTypes.has(edge.type)) return false;
    if (until && edge.created_at && Date.parse(edge.created_at) > until) return false;
    return true;
  });

  return {
    ...graph,
    nodes: nodes.filter((n) => kept.has(n.id)),
    edges,
  };
}
