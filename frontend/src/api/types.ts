/** Mirrors the backend Pydantic contracts in backend/app/schemas. */

export type Assertion = 'observed' | 'inferred' | 'correlated' | 'unverified';
export type MatchBand = 'possible' | 'probable' | 'strong' | 'confirmed';
export type Role = 'admin' | 'analyst' | 'viewer';
export type InvestigationStatus = 'draft' | 'running' | 'completed' | 'failed' | 'cancelled';
export type Stage =
  | 'idle'
  | 'discovery'
  | 'validation'
  | 'correlation'
  | 'graph_expansion'
  | 'analysis'
  | 'done';

export interface User {
  id: string;
  email: string;
  username: string;
  role: Role;
  is_active: boolean;
  created_at: string;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface Target {
  id: string;
  type: string;
  value: string;
  normalized: string;
  created_at: string;
}

export interface GraphStats {
  entities: number;
  relationships: number;
  sources: number;
  strong_correlations: number;
  possible_matches: number;
  by_type: Record<string, number>;
  by_assertion: Record<string, number>;
}

export interface Investigation {
  id: string;
  name: string;
  description: string | null;
  status: InvestigationStatus;
  stage: Stage;
  tags: string[];
  owner_id: string;
  created_at: string;
  updated_at: string;
  targets: Target[];
  stats: GraphStats;
}

export interface GraphNode {
  id: string;
  type: string;
  label: string;
  canonical_key: string;
  confidence: number;
  cluster: string;
  degree: number;
  depth: number;
  is_target: boolean;
  sources: string[];
  first_seen: string | null;
  last_seen: string | null;
  is_cluster: boolean;
  attributes: Record<string, unknown>;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  type: string;
  confidence: number;
  assertion: Assertion;
  provider: string;
  is_correlation: boolean;
  created_at: string | null;
  why: string;
}

export interface GraphCluster {
  id: string;
  label: string;
  size: number;
}

export interface GraphPayload {
  nodes: GraphNode[];
  edges: GraphEdge[];
  clusters: GraphCluster[];
  clustered: boolean;
  stats: GraphStats;
}

export interface EvidenceRecord {
  id: string;
  content_type: string;
  sha256: string;
  excerpt: string;
  payload: Record<string, unknown>;
  captured_at: string;
}

export interface Observation {
  id: string;
  entity_id: string | null;
  provider: string;
  source: string;
  kind: string;
  url: string | null;
  observed_at: string;
  confidence: number;
  assertion: Assertion;
  data: Record<string, unknown>;
  evidence: EvidenceRecord[];
}

export interface Relationship {
  id: string;
  source_entity_id: string;
  target_entity_id: string;
  type: string;
  confidence: number;
  assertion: Assertion;
  provider: string;
  evidence: Record<string, unknown>;
  created_at: string;
}

export interface RelationshipDetail extends Relationship {
  source_label: string;
  target_label: string;
  why: string;
  evidence_url: string | null;
  observation: Observation | null;
}

export interface Identifier {
  kind: string;
  value: string;
  normalized: string;
}

export interface EntityDetail {
  id: string;
  investigation_id: string;
  type: string;
  label: string;
  canonical_key: string;
  confidence: number;
  attributes: Record<string, unknown>;
  sources: string[];
  first_seen: string;
  last_seen: string;
  depth: number;
  identifiers: Identifier[];
  observation_count: number;
  relationships: Relationship[];
  external_links: string[];
  recent_observations: Observation[];
}

export interface TimelineEvent {
  at: string;
  kind: string;
  entity_id: string | null;
  label: string;
  description: string;
  provider: string;
  confidence: number;
  url: string | null;
}

export interface IdentityMatch {
  id: string;
  entity_a_id: string;
  entity_b_id: string;
  entity_a_label: string;
  entity_b_label: string;
  score: number;
  band: MatchBand;
  band_label: string;
  reasons: string[];
  explanation: string;
  created_at: string;
}

export interface ProviderProgress {
  provider: string;
  total: number;
  done: number;
  failed: number;
  percent: number;
  status: string;
}

export interface Progress {
  investigation_id: string;
  status: InvestigationStatus;
  stage: Stage;
  percent: number;
  providers: ProviderProgress[];
  correlation_percent: number;
}

export interface Source {
  name: string;
  type: string;
  enabled: boolean;
  requires_api_key: boolean;
  configured: boolean;
  rate_limit: { rpm: number; concurrency: number; timeout_seconds: number };
  reliability: number;
  cost: string;
  coverage: Record<string, unknown>;
  accepts: string[];
  emits: string[];
  recursive: boolean;
  description: string;
  health: 'ok' | 'degraded' | 'unavailable' | 'disabled';
  health_detail: string;
}

export interface PathStep {
  entity_id: string;
  label: string;
  type: string;
  via: string | null;
  via_confidence: number | null;
  why: string | null;
}

export interface PathResult {
  found: boolean;
  steps: PathStep[];
  markdown: string;
}

export interface SearchHit {
  entity_id: string;
  label: string;
  type: string;
  confidence: number;
  matched_on: string;
  snippet: string;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export type LiveEvent =
  | { event: 'connected'; investigation_id: string }
  | { event: 'job_started'; provider: string; job_id: string; target: string }
  | { event: 'job_finished'; provider: string; job_id: string; status: string; observations: number; error: string | null }
  | { event: 'provider_skipped'; provider: string; reason: string }
  | { event: 'entity_discovered'; entity_id: string; type: string; node: GraphNode }
  | { event: 'relationship_discovered'; relationship_id: string; edge: GraphEdge }
  | { event: 'match_found'; score: number; band: MatchBand; a: { id: string; label: string }; b: { id: string; label: string }; reasons: string[] }
  | { event: 'stage_changed'; stage: Stage; depth?: number }
  | { event: 'investigation_completed'; stats: GraphStats; jobs: number; matches: number; errors: string[] }
  | { event: 'error'; provider: string; message: string };

// -- social intelligence layer -------------------------------------------------

export interface Finding {
  kind: 'identity' | 'interaction' | 'pivot' | 'reach';
  severity: 'strong' | 'probable' | 'possible' | 'informational';
  headline: string;
  detail: string;
  entity_ids: string[];
  evidence_ids: string[];
  metrics: Record<string, number>;
}

export interface FindingsResult {
  summary: Record<string, number>;
  findings: Finding[];
}

/** Public activity between two accounts. A count — never an identity claim. */
export interface Interaction {
  actor_id: string;
  target_id: string;
  actor_label: string;
  target_label: string;
  comments: number;
  replies: number;
  mentions: number;
  total: number;
  strength: 'single' | 'occasional' | 'regular' | 'frequent';
  description: string;
  evidence_ids: string[];
  identity_match: string;
}
