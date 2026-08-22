import { beforeEach, describe, expect, it } from 'vitest';
import { useInvestigationStore } from '@/store/investigation';
import type { GraphEdge, GraphNode, GraphPayload } from '@/api/types';

const emptyGraph: GraphPayload = {
  nodes: [],
  edges: [],
  clusters: [],
  clustered: false,
  stats: {
    entities: 0,
    relationships: 0,
    sources: 0,
    strong_correlations: 0,
    possible_matches: 0,
    by_type: {},
    by_assertion: {},
  },
};

const sampleNode: GraphNode = {
  id: 'ent_1',
  type: 'social_account',
  label: 'GitHub/example_user',
  canonical_key: 'github:user:example_user',
  confidence: 0.94,
  display_confidence: 0.94,
  is_stale: false,
  staleness_note: null,
  cluster: 'github',
  degree: 1,
  depth: 1,
  is_target: false,
  sources: ['github'],
  first_seen: '2026-01-01T00:00:00Z',
  last_seen: '2026-01-01T00:00:00Z',
  is_cluster: false,
  attributes: {},
};

const sampleEdge: GraphEdge = {
  id: 'rel_1',
  source: 'ent_0',
  target: 'ent_1',
  type: 'USES_USERNAME',
  confidence: 0.9,
  assertion: 'observed',
  provider: 'github',
  is_correlation: false,
  created_at: '2026-01-01T00:00:00Z',
  why: 'public profile',
};

describe('live event handling', () => {
  beforeEach(() => {
    useInvestigationStore.setState({
      graph: { ...emptyGraph },
      activity: {},
      errors: [],
      stage: 'idle',
    });
  });

  it('adds a discovered entity to the graph immediately', () => {
    useInvestigationStore
      .getState()
      .applyEvent({ event: 'entity_discovered', entity_id: 'ent_1', type: 'social_account', node: sampleNode });
    expect(useInvestigationStore.getState().graph?.nodes).toHaveLength(1);
  });

  it('does not duplicate an entity reported twice', () => {
    const { applyEvent } = useInvestigationStore.getState();
    const event = { event: 'entity_discovered', entity_id: 'ent_1', type: 'social_account', node: sampleNode } as const;
    applyEvent(event);
    applyEvent(event);
    expect(useInvestigationStore.getState().graph?.nodes).toHaveLength(1);
  });

  it('adds relationships and tracks provider activity', () => {
    const { applyEvent } = useInvestigationStore.getState();
    applyEvent({ event: 'relationship_discovered', relationship_id: 'rel_1', edge: sampleEdge });
    applyEvent({ event: 'job_started', provider: 'github', job_id: 'j1', target: 'example_user' });
    expect(useInvestigationStore.getState().graph?.edges).toHaveLength(1);
    expect(useInvestigationStore.getState().activity.github?.status).toBe('running');

    applyEvent({
      event: 'job_finished',
      provider: 'github',
      job_id: 'j1',
      status: 'ok',
      observations: 4,
      error: null,
    });
    expect(useInvestigationStore.getState().activity.github).toMatchObject({
      status: 'done',
      observations: 4,
    });
  });

  it('records a skipped provider with the reason', () => {
    useInvestigationStore
      .getState()
      .applyEvent({ event: 'provider_skipped', provider: 'maigret', reason: 'binary not installed' });
    expect(useInvestigationStore.getState().activity.maigret).toMatchObject({
      status: 'skipped',
      message: 'binary not installed',
    });
  });

  it('tracks the pipeline stage and collects errors', () => {
    const { applyEvent } = useInvestigationStore.getState();
    applyEvent({ event: 'stage_changed', stage: 'correlation' });
    expect(useInvestigationStore.getState().stage).toBe('correlation');
    applyEvent({ event: 'error', provider: 'dns', message: 'timeout' });
    expect(useInvestigationStore.getState().errors).toContain('dns: timeout');
  });
});

describe('filter interactions', () => {
  beforeEach(() => useInvestigationStore.getState().resetFilters());

  it('toggles entity types on and off', () => {
    const { toggleEntityType } = useInvestigationStore.getState();
    toggleEntityType('domain');
    expect(useInvestigationStore.getState().filters.entityTypes.has('domain')).toBe(true);
    toggleEntityType('domain');
    expect(useInvestigationStore.getState().filters.entityTypes.has('domain')).toBe(false);
  });

  it('selecting a node clears any selected edge', () => {
    const state = useInvestigationStore.getState();
    state.selectEdge('rel_1');
    expect(useInvestigationStore.getState().selectedEdgeId).toBe('rel_1');
    state.selectNode('ent_1');
    expect(useInvestigationStore.getState().selectedEdgeId).toBeNull();
    expect(useInvestigationStore.getState().selectedNodeId).toBe('ent_1');
  });
});
