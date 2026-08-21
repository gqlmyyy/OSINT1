import { describe, expect, it } from 'vitest';
import { toElements, layoutOptions } from '@/graph/elements';
import { visibleGraph, type GraphFilters } from '@/store/investigation';
import type { GraphEdge, GraphNode, GraphPayload } from '@/api/types';

function node(overrides: Partial<GraphNode> & { id: string }): GraphNode {
  return {
    type: 'social_account',
    label: `node-${overrides.id}`,
    canonical_key: `github:user:${overrides.id}`,
    confidence: 0.9,
    cluster: 'github',
    degree: 2,
    depth: 1,
    is_target: false,
    sources: ['github'],
    first_seen: '2026-01-01T00:00:00Z',
    last_seen: '2026-01-02T00:00:00Z',
    is_cluster: false,
    attributes: {},
    ...overrides,
  };
}

function edge(overrides: Partial<GraphEdge> & { id: string; source: string; target: string }): GraphEdge {
  return {
    type: 'LINKS_TO',
    confidence: 0.8,
    assertion: 'observed',
    provider: 'github',
    is_correlation: false,
    created_at: '2026-01-01T00:00:00Z',
    why: 'profile link',
    ...overrides,
  };
}

function payload(nodes: GraphNode[], edges: GraphEdge[]): GraphPayload {
  return {
    nodes,
    edges,
    clusters: [],
    clustered: false,
    stats: {
      entities: nodes.length,
      relationships: edges.length,
      sources: 1,
      strong_correlations: 0,
      possible_matches: 0,
      by_type: {},
      by_assertion: {},
    },
  };
}

const baseFilters = (overrides: Partial<GraphFilters> = {}): GraphFilters => ({
  minConfidence: 0,
  entityTypes: new Set(),
  edgeTypes: new Set(),
  hiddenNodes: new Set(),
  collapsed: new Set(),
  search: '',
  until: null,
  showCorrelations: true,
  ...overrides,
});

describe('toElements', () => {
  it('sizes nodes by degree so hubs stand out', () => {
    const graph = payload(
      [node({ id: 'a', degree: 10 }), node({ id: 'b', degree: 1 })],
      [],
    );
    const elements = toElements(graph);
    const sizes = elements.map((element) => (element.data as { size: number }).size);
    expect(sizes[0]!).toBeGreaterThan(sizes[1]!);
  });

  it('encodes edge confidence as line width and assertion as colour', () => {
    const graph = payload(
      [node({ id: 'a' }), node({ id: 'b' })],
      [
        edge({ id: 'e1', source: 'a', target: 'b', confidence: 0.95 }),
        edge({ id: 'e2', source: 'b', target: 'a', confidence: 0.2, assertion: 'unverified' }),
      ],
    );
    const edges = toElements(graph).filter((element) => element.group === 'edges');
    const strong = edges[0]!.data as { width: number; color: string };
    const weak = edges[1]!.data as { width: number; color: string };
    expect(strong.width).toBeGreaterThan(weak.width);
    expect(strong.color).not.toBe(weak.color);
  });

  it('truncates long labels but keeps the full text available', () => {
    const long = 'x'.repeat(80);
    const elements = toElements(payload([node({ id: 'a', label: long })], []));
    const data = elements[0]!.data as { label: string; fullLabel: string };
    expect(data.label.length).toBeLessThan(long.length);
    expect(data.fullLabel).toBe(long);
  });

  it('skips animation for large graphs', () => {
    const small = layoutOptions('cose', 50) as { animate: boolean; numIter: number };
    const large = layoutOptions('cose', 5000) as { animate: boolean; numIter: number };
    expect(small.animate).toBe(true);
    expect(large.animate).toBe(false);
    expect(large.numIter).toBeLessThan(small.numIter);
  });
});

describe('visibleGraph', () => {
  const graph = payload(
    [
      node({ id: 'a', confidence: 0.95, type: 'username', label: 'example_user' }),
      node({ id: 'b', confidence: 0.6, type: 'social_account', label: 'github/example' }),
      node({ id: 'c', confidence: 0.3, type: 'url', label: 'https://example.com' }),
    ],
    [
      edge({ id: 'e1', source: 'a', target: 'b', confidence: 0.9 }),
      edge({ id: 'e2', source: 'b', target: 'c', confidence: 0.4 }),
      edge({ id: 'e3', source: 'a', target: 'c', confidence: 0.86, is_correlation: true, assertion: 'correlated' }),
    ],
  );

  it('drops nodes and edges below the confidence floor', () => {
    const result = visibleGraph(graph, baseFilters({ minConfidence: 0.7 }));
    expect(result.nodes.map((n) => n.id)).toEqual(['a']);
    expect(result.edges).toHaveLength(0);
  });

  it('filters by entity type', () => {
    const result = visibleGraph(graph, baseFilters({ entityTypes: new Set(['username', 'url']) }));
    expect(result.nodes.map((n) => n.id).sort()).toEqual(['a', 'c']);
    expect(result.edges.map((e) => e.id)).toEqual(['e3']);
  });

  it('hides individual nodes and the edges that touch them', () => {
    const result = visibleGraph(graph, baseFilters({ hiddenNodes: new Set(['b']) }));
    expect(result.nodes.map((n) => n.id).sort()).toEqual(['a', 'c']);
    expect(result.edges.map((e) => e.id)).toEqual(['e3']);
  });

  it('can hide correlation edges without hiding their nodes', () => {
    const result = visibleGraph(graph, baseFilters({ showCorrelations: false }));
    expect(result.nodes).toHaveLength(3);
    expect(result.edges.map((e) => e.id)).toEqual(['e1', 'e2']);
  });

  it('collapsing a node hides what hangs off it', () => {
    const result = visibleGraph(graph, baseFilters({ collapsed: new Set(['a']) }));
    expect(result.nodes.map((n) => n.id)).toEqual(['a']);
  });

  it('scopes the graph to a point in time', () => {
    const timed = payload(
      [
        node({ id: 'old', first_seen: '2025-01-01T00:00:00Z' }),
        node({ id: 'new', first_seen: '2026-06-01T00:00:00Z' }),
      ],
      [],
    );
    const result = visibleGraph(timed, baseFilters({ until: '2025-06-01T00:00:00Z' }));
    expect(result.nodes.map((n) => n.id)).toEqual(['old']);
  });

  it('searches on label', () => {
    const result = visibleGraph(graph, baseFilters({ search: 'GITHUB' }));
    expect(result.nodes.map((n) => n.id)).toEqual(['b']);
  });
});
