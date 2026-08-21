import type {
  EntityDetail,
  FindingsResult,
  Interaction,
  GraphPayload,
  IdentityMatch,
  Investigation,
  Observation,
  Page,
  PathResult,
  Progress,
  Relationship,
  RelationshipDetail,
  SearchHit,
  Source,
  TimelineEvent,
  TokenPair,
  User,
} from './types';

const BASE = '/api/v1';
const ACCESS_KEY = 'graphintel.access';
const REFRESH_KEY = 'graphintel.refresh';

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly detail?: unknown,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export const tokens = {
  access: () => localStorage.getItem(ACCESS_KEY),
  refresh: () => localStorage.getItem(REFRESH_KEY),
  set(pair: TokenPair) {
    localStorage.setItem(ACCESS_KEY, pair.access_token);
    localStorage.setItem(REFRESH_KEY, pair.refresh_token);
  },
  clear() {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
  },
};

interface FieldError {
  loc?: string[];
  msg?: string;
}

/**
 * Turn an error payload into something the user can act on.
 *
 * Ordering matters: a 422 carries BOTH a generic `message` ("request failed validation")
 * and a `fields` list naming the rule that was broken. Reading `message` first made the
 * field list unreachable, so every rejection looked identical and the user had no way to
 * learn what to change. Most specific wins.
 */
export function messageFrom(status: number, detail: unknown): string {
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object') {
    const record = detail as Record<string, unknown>;

    if (Array.isArray(record.fields) && record.fields.length > 0) {
      const described = record.fields
        .map((entry) => {
          const field = entry as FieldError;
          // `loc` is ["body", "password"]; the field name is the part worth showing.
          const name = field.loc?.filter((part) => part !== 'body').slice(-1)[0] ?? 'field';
          // "Value error, " is Pydantic's internal prefix — noise to whoever is reading.
          const reason = (field.msg ?? 'invalid').replace(/^Value error,\s*/i, '');
          // Custom validators already name the field ("password is not varied enough"),
          // so prefixing it again would read as a stutter.
          return reason.toLowerCase().startsWith(name.toLowerCase())
            ? reason
            : `${name}: ${reason}`;
        })
        .filter(Boolean);
      if (described.length > 0) return described.join('; ');
    }

    if (typeof record.message === 'string') return record.message;
  }
  return `request failed (${status})`;
}

let refreshing: Promise<boolean> | null = null;

async function refreshAccessToken(): Promise<boolean> {
  const refresh = tokens.refresh();
  if (!refresh) return false;
  refreshing ??= (async () => {
    try {
      const response = await fetch(`${BASE}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refresh }),
      });
      if (!response.ok) return false;
      tokens.set((await response.json()) as TokenPair);
      return true;
    } finally {
      refreshing = null;
    }
  })();
  return refreshing;
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  raw?: boolean;
  retry?: boolean;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, raw = false, retry = true } = options;
  const headers: Record<string, string> = {};
  const access = tokens.access();
  if (access) headers.Authorization = `Bearer ${access}`;
  if (body !== undefined) headers['Content-Type'] = 'application/json';

  const response = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  // One transparent refresh attempt, then surface the failure to the caller.
  if (response.status === 401 && retry && (await refreshAccessToken())) {
    return request<T>(path, { ...options, retry: false });
  }
  if (!response.ok) {
    let detail: unknown;
    try {
      detail = (await response.json()).detail;
    } catch {
      detail = await response.text();
    }
    throw new ApiError(response.status, messageFrom(response.status, detail), detail);
  }
  if (raw) return (await response.blob()) as unknown as T;
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function query(params: Record<string, string | number | boolean | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value));
  }
  const encoded = search.toString();
  return encoded ? `?${encoded}` : '';
}

export interface GraphQuery extends Record<string, string | number | undefined> {
  min_confidence?: number;
  types?: string;
  edge_types?: string;
  since?: string;
  until?: string;
  expand?: string;
  q?: string;
  limit?: number;
}

export const api = {
  // -- auth ----------------------------------------------------------------
  register: (payload: { email: string; username: string; password: string }) =>
    request<User>('/auth/register', { method: 'POST', body: payload }),
  login: (payload: { username: string; password: string }) =>
    request<TokenPair>('/auth/login', { method: 'POST', body: payload }),
  me: () => request<User>('/auth/me'),

  // -- investigations ------------------------------------------------------
  listInvestigations: (params: { limit?: number; offset?: number; q?: string } = {}) =>
    request<Page<Investigation>>(`/investigations${query(params)}`),
  getInvestigation: (id: string) => request<Investigation>(`/investigations/${id}`),
  createInvestigation: (payload: {
    name: string;
    description?: string;
    tags?: string[];
    targets?: { value: string; type?: string }[];
  }) => request<Investigation>('/investigations', { method: 'POST', body: payload }),
  deleteInvestigation: (id: string) =>
    request<void>(`/investigations/${id}`, { method: 'DELETE' }),
  addTargets: (id: string, targets: { value: string; type?: string }[]) =>
    request<Target[]>(`/investigations/${id}/targets`, { method: 'POST', body: { targets } }),
  startScan: (id: string, payload: { providers?: string[]; max_depth?: number; recursive?: boolean }) =>
    request<{ task_id: string; providers: string[] }>(`/investigations/${id}/scan`, {
      method: 'POST',
      body: payload,
    }),
  cancelScan: (id: string) => request<Progress>(`/investigations/${id}/cancel`, { method: 'POST' }),
  progress: (id: string) => request<Progress>(`/investigations/${id}/progress`),
  graph: (id: string, params: GraphQuery = {}) =>
    request<GraphPayload>(`/investigations/${id}/graph${query(params)}`),
  timeline: (id: string, entityId?: string) =>
    request<TimelineEvent[]>(`/investigations/${id}/timeline${query({ entity_id: entityId })}`),
  matches: (id: string) => request<IdentityMatch[]>(`/investigations/${id}/matches`),
  findings: (id: string) => request<FindingsResult>(`/investigations/${id}/findings`),
  interactions: (id: string) => request<Interaction[]>(`/investigations/${id}/interactions`),
  observations: (id: string, params: { provider?: string; limit?: number } = {}) =>
    request<Page<Observation>>(`/investigations/${id}/observations${query(params)}`),
  relationships: (id: string) => request<Relationship[]>(`/investigations/${id}/relationships`),
  search: (id: string, q: string) =>
    request<{ query: string; hits: SearchHit[]; total: number }>(
      `/investigations/${id}/search${query({ q })}`,
    ),
  exportInvestigation: (id: string, payload: { format: string; scope: string }) =>
    request<Blob>(`/investigations/${id}/export`, { method: 'POST', body: payload, raw: true }),

  // -- entities ------------------------------------------------------------
  entity: (id: string) => request<EntityDetail>(`/entities/${id}`),
  entityTimeline: (id: string) => request<TimelineEvent[]>(`/entities/${id}/timeline`),
  expandEntity: (id: string) => request<{ task_id: string }>(`/entities/${id}/expand`, { method: 'POST' }),
  relationship: (id: string) => request<RelationshipDetail>(`/relationships/${id}`),

  // -- analytics -----------------------------------------------------------
  shortestPath: (id: string, sourceId: string, targetId: string) =>
    request<PathResult>(`/investigations/${id}/analytics/shortest-path`, {
      method: 'POST',
      body: { source_id: sourceId, target_id: targetId },
    }),
  centrality: (id: string, metric = 'degree') =>
    request<{ id: string; label: string; type: string; score: number; degree: number }[]>(
      `/investigations/${id}/analytics/centrality${query({ metric, limit: 15 })}`,
    ),
  clusters: (id: string) =>
    request<{ index: number; size: number; label: string }[]>(
      `/investigations/${id}/analytics/clusters`,
    ),
  duplicates: (id: string) =>
    request<{ reason: string; kind: string; value: string; labels: string[] }[]>(
      `/investigations/${id}/analytics/duplicates`,
    ),

  // -- sources & demo ------------------------------------------------------
  sources: () => request<Source[]>('/sources'),
  updateSource: (name: string, payload: { enabled?: boolean; rpm?: number }) =>
    request<Source>(`/sources/${name}`, { method: 'PATCH', body: payload }),
  seedDemo: () =>
    request<{ investigation_id: string; name: string }>('/demo/seed', { method: 'POST' }),
  aiStatus: () => request<{ enabled: boolean; provider: string; model: string; reachable: boolean }>(
    '/ai/status',
  ),
};

export type Target = import('./types').Target;
