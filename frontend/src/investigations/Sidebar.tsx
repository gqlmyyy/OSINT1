import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import clsx from 'clsx';
import { api } from '@/api/client';
import type { Investigation } from '@/api/types';
import { BandBadge, Button, Confidence, Empty, Input } from '@/components/ui';
import { colorForType } from '@/graph/style';
import { useInvestigationStore } from '@/store/investigation';
import { ScanProgress } from './ScanProgress';

type Tab = 'targets' | 'entities' | 'matches' | 'sources' | 'filters';

const TABS: { id: Tab; label: string }[] = [
  { id: 'targets', label: 'Targets' },
  { id: 'entities', label: 'Entities' },
  { id: 'matches', label: 'Matches' },
  { id: 'sources', label: 'Sources' },
  { id: 'filters', label: 'Filters' },
];

export function Sidebar({ investigation }: { investigation: Investigation }) {
  const [tab, setTab] = useState<Tab>('targets');

  return (
    <div className="flex h-full min-h-0 flex-col border-r border-line bg-ink-850">
      <nav className="flex shrink-0 border-b border-line" role="tablist">
        {TABS.map((item) => (
          <button
            key={item.id}
            role="tab"
            aria-selected={tab === item.id}
            onClick={() => setTab(item.id)}
            className={clsx(
              'flex-1 border-b-2 px-1 py-2 text-2xs font-medium uppercase tracking-wide transition',
              tab === item.id
                ? 'border-accent text-fg'
                : 'border-transparent text-fg-dim hover:text-fg-muted',
            )}
          >
            {item.label}
          </button>
        ))}
      </nav>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {tab === 'targets' && <TargetsTab investigation={investigation} />}
        {tab === 'entities' && <EntitiesTab investigationId={investigation.id} />}
        {tab === 'matches' && <MatchesTab investigationId={investigation.id} />}
        {tab === 'sources' && <SourcesTab />}
        {tab === 'filters' && <FiltersTab investigationId={investigation.id} />}
      </div>
    </div>
  );
}

function TargetsTab({ investigation }: { investigation: Investigation }) {
  const stats = investigation.stats;
  return (
    <div>
      <ul className="divide-y divide-line/60">
        {investigation.targets.map((target) => (
          <li key={target.id} className="px-3 py-2">
            <p className="break-all text-xs text-fg">{target.value}</p>
            <p className="text-2xs uppercase tracking-wider text-fg-dim">{target.type}</p>
          </li>
        ))}
        {investigation.targets.length === 0 && <Empty>No targets yet.</Empty>}
      </ul>
      <div className="border-t border-line px-3 py-3">
        <h3 className="mb-2 text-2xs font-semibold uppercase tracking-wider text-fg-dim">Summary</h3>
        <dl className="grid grid-cols-2 gap-2">
          <Stat label="Entities" value={stats.entities} />
          <Stat label="Relationships" value={stats.relationships} />
          <Stat label="Sources" value={stats.sources} />
          <Stat label="Strong correlations" value={stats.strong_correlations} />
          <Stat label="Possible matches" value={stats.possible_matches} />
        </dl>
      </div>
      <ScanProgress investigationId={investigation.id} />
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded border border-line bg-ink-800 px-2 py-1.5">
      <dd className="text-base font-semibold tabular-nums text-fg">{value}</dd>
      <dt className="text-2xs leading-tight text-fg-dim">{label}</dt>
    </div>
  );
}

function EntitiesTab({ investigationId }: { investigationId: string }) {
  const [query, setQuery] = useState('');
  const graph = useInvestigationStore((s) => s.graph);
  const selectNode = useInvestigationStore((s) => s.selectNode);

  const { data: hits } = useQuery({
    queryKey: ['search', investigationId, query],
    queryFn: () => api.search(investigationId, query),
    enabled: query.trim().length > 1,
  });

  const nodes = (graph?.nodes ?? []).filter((n) => !n.is_cluster);
  const listed = hits?.hits.length
    ? nodes.filter((n) => hits.hits.some((h) => h.entity_id === n.id))
    : nodes;

  return (
    <div>
      <div className="border-b border-line p-2">
        <Input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search entities, identifiers, evidence…"
          aria-label="Search inside this investigation"
        />
      </div>
      <ul className="divide-y divide-line/60">
        {[...listed]
          .sort((a, b) => b.degree - a.degree || a.label.localeCompare(b.label))
          .slice(0, 300)
          .map((node) => (
            <li key={node.id}>
              <button
                onClick={() => selectNode(node.id)}
                className="flex w-full items-center gap-2 px-3 py-1.5 text-left transition hover:bg-ink-800"
              >
                <span
                  className="h-2 w-2 shrink-0 rounded-full"
                  style={{ backgroundColor: colorForType(node.type) }}
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs text-fg">{node.label}</span>
                  <span className="block text-2xs text-fg-dim">
                    {node.type} · {node.degree} link{node.degree === 1 ? '' : 's'}
                  </span>
                </span>
                <Confidence
                  value={node.display_confidence}
                  rawValue={node.confidence}
                  isStale={node.is_stale}
                  note={node.staleness_note}
                />
              </button>
            </li>
          ))}
        {listed.length === 0 && <Empty>No entities match.</Empty>}
      </ul>
    </div>
  );
}

function MatchesTab({ investigationId }: { investigationId: string }) {
  const { data } = useQuery({
    queryKey: ['matches', investigationId],
    queryFn: () => api.matches(investigationId),
  });
  const selectNode = useInvestigationStore((s) => s.selectNode);

  if (!data?.length) {
    return <Empty>No identity candidates above the confidence threshold.</Empty>;
  }
  return (
    <ul className="divide-y divide-line/60">
      {data.map((match) => (
        <li key={match.id} className="px-3 py-2">
          <div className="flex items-center justify-between gap-2">
            <BandBadge band={match.band} label={match.band_label} />
            <span className="tabular-nums text-2xs text-fg-muted">
              {Math.round(match.score * 100)}%
            </span>
          </div>
          <div className="mt-1.5 space-y-0.5 text-xs">
            <button className="block truncate text-left text-fg hover:text-accent" onClick={() => selectNode(match.entity_a_id)}>
              {match.entity_a_label}
            </button>
            <button className="block truncate text-left text-fg hover:text-accent" onClick={() => selectNode(match.entity_b_id)}>
              {match.entity_b_label}
            </button>
          </div>
          <ul className="mt-1.5 space-y-0.5">
            {match.reasons.map((reason) => (
              <li
                key={reason}
                className={clsx('text-2xs', reason.startsWith('+') ? 'text-observed' : 'text-inferred')}
              >
                {reason}
              </li>
            ))}
          </ul>
        </li>
      ))}
    </ul>
  );
}

function SourcesTab() {
  const { data, refetch } = useQuery({ queryKey: ['sources'], queryFn: api.sources });

  if (!data) return <Empty>Loading sources…</Empty>;
  return (
    <ul className="divide-y divide-line/60">
      {data.map((source) => (
        <li key={source.name} className="px-3 py-2">
          <div className="flex items-center gap-2">
            <button
              onClick={async () => {
                await api.updateSource(source.name, { enabled: !source.enabled });
                await refetch();
              }}
              aria-label={`${source.enabled ? 'Disable' : 'Enable'} ${source.name}`}
              className={clsx(
                'h-3.5 w-3.5 shrink-0 rounded-sm border text-2xs leading-none',
                source.enabled ? 'border-observed bg-observed/20 text-observed' : 'border-line',
              )}
            >
              {source.enabled ? '✓' : ''}
            </button>
            <span className="flex-1 truncate text-xs text-fg">{source.name}</span>
            <span
              className={clsx(
                'text-2xs',
                source.health === 'ok' && 'text-observed',
                source.health === 'degraded' && 'text-inferred',
                source.health === 'unavailable' && 'text-unverified',
                source.health === 'disabled' && 'text-fg-dim',
              )}
            >
              {source.health}
            </span>
          </div>
          <p className="mt-0.5 pl-5 text-2xs text-fg-dim">{source.description}</p>
          {source.health !== 'ok' && source.health_detail && (
            <p className="mt-0.5 pl-5 text-2xs text-inferred/80">{source.health_detail}</p>
          )}
          <p className="mt-0.5 pl-5 text-2xs text-fg-dim">
            accepts {source.accepts.join(', ')} · {source.rate_limit.rpm}/min
          </p>
        </li>
      ))}
    </ul>
  );
}

function FiltersTab({ investigationId }: { investigationId: string }) {
  const graph = useInvestigationStore((s) => s.graph);
  const filters = useInvestigationStore((s) => s.filters);
  const setFilter = useInvestigationStore((s) => s.setFilter);
  const toggleEntityType = useInvestigationStore((s) => s.toggleEntityType);
  const reset = useInvestigationStore((s) => s.resetFilters);

  const types = Object.entries(graph?.stats.by_type ?? {}).sort((a, b) => b[1] - a[1]);
  const { data: clusters } = useQuery({
    queryKey: ['clusters', investigationId],
    queryFn: () => api.clusters(investigationId),
  });

  return (
    <div className="space-y-4 p-3">
      <div>
        <label className="mb-1 block text-2xs uppercase tracking-wider text-fg-dim">
          Minimum confidence: {Math.round(filters.minConfidence * 100)}%
        </label>
        <input
          type="range"
          min={0}
          max={0.95}
          step={0.05}
          value={filters.minConfidence}
          onChange={(event) => setFilter('minConfidence', Number(event.target.value))}
          className="w-full accent-accent"
        />
      </div>

      <label className="flex items-center gap-2 text-xs text-fg">
        <input
          type="checkbox"
          checked={filters.showCorrelations}
          onChange={(event) => setFilter('showCorrelations', event.target.checked)}
          className="accent-accent"
        />
        Show correlation edges
      </label>

      <div>
        <p className="mb-1 text-2xs uppercase tracking-wider text-fg-dim">Entity types</p>
        <div className="flex flex-wrap gap-1">
          {types.map(([type, count]) => {
            const active = filters.entityTypes.size === 0 || filters.entityTypes.has(type);
            return (
              <button
                key={type}
                onClick={() => toggleEntityType(type)}
                className={clsx(
                  'rounded border px-1.5 py-0.5 text-2xs transition',
                  active ? 'border-line-bright text-fg' : 'border-line text-fg-dim opacity-50',
                )}
                style={active ? { borderColor: colorForType(type) } : undefined}
              >
                {type} {count}
              </button>
            );
          })}
        </div>
      </div>

      {clusters && clusters.length > 0 && (
        <div>
          <p className="mb-1 text-2xs uppercase tracking-wider text-fg-dim">Detected clusters</p>
          <ul className="space-y-0.5 text-2xs text-fg-muted">
            {clusters.slice(0, 8).map((cluster) => (
              <li key={cluster.index}>
                {cluster.label} — {cluster.size} node{cluster.size === 1 ? '' : 's'}
              </li>
            ))}
          </ul>
        </div>
      )}

      <Button variant="ghost" onClick={reset} className="w-full">
        Reset filters
      </Button>
    </div>
  );
}
