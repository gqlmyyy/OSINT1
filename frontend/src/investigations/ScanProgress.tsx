import { useQuery } from '@tanstack/react-query';
import clsx from 'clsx';
import { api } from '@/api/client';
import type { Stage } from '@/api/types';
import { useInvestigationStore } from '@/store/investigation';

/** Pipeline stages, shown so the analyst always knows what the system is doing (spec 37). */
const STAGES: { id: Stage; label: string }[] = [
  { id: 'discovery', label: 'Discovery' },
  { id: 'validation', label: 'Validation' },
  { id: 'correlation', label: 'Correlation' },
  { id: 'graph_expansion', label: 'Expansion' },
  { id: 'analysis', label: 'Analysis' },
];

export function StageTrail({ stage }: { stage: Stage }) {
  const reached = STAGES.findIndex((s) => s.id === stage);
  const done = stage === 'done';

  return (
    <ol className="flex items-center gap-1" aria-label="Investigation pipeline">
      {STAGES.map((item, index) => {
        const active = !done && index === reached;
        const complete = done || (reached >= 0 && index < reached);
        return (
          <li key={item.id} className="flex items-center gap-1">
            <span
              className={clsx(
                'whitespace-nowrap rounded px-1.5 py-0.5 text-2xs transition',
                active && 'bg-accent/20 text-accent',
                complete && 'text-observed',
                !active && !complete && 'text-fg-dim',
              )}
            >
              {item.label}
            </span>
            {index < STAGES.length - 1 && <span className="text-fg-dim">›</span>}
          </li>
        );
      })}
    </ol>
  );
}

export function ScanProgress({ investigationId }: { investigationId: string }) {
  const activity = useInvestigationStore((s) => s.activity);
  const errors = useInvestigationStore((s) => s.errors);

  const { data } = useQuery({
    queryKey: ['progress', investigationId],
    queryFn: () => api.progress(investigationId),
    refetchInterval: (query) =>
      query.state.data?.status === 'running' ? 2000 : false,
  });

  const rows = data?.providers ?? [];
  const live = Object.values(activity);
  if (rows.length === 0 && live.length === 0) return null;

  const merged = rows.length > 0
    ? rows.map((row) => ({
        provider: row.provider,
        percent: row.percent,
        state: row.status,
        note: undefined as string | undefined,
      }))
    : live.map((item) => ({
        provider: item.provider,
        percent: item.status === 'running' ? 45 : 100,
        state: item.status,
        note: item.message,
      }));

  return (
    <div className="border-t border-line px-3 py-3">
      <h3 className="mb-2 text-2xs font-semibold uppercase tracking-wider text-fg-dim">
        Provider progress
      </h3>
      <ul className="space-y-1.5">
        {merged.map((row) => (
          <li key={row.provider}>
            <div className="flex items-center justify-between text-2xs">
              <span className="text-fg-muted">{row.provider}</span>
              <span className="tabular-nums text-fg-dim">{row.percent}%</span>
            </div>
            <div className="mt-0.5 h-1 overflow-hidden rounded-full bg-ink-700">
              <div
                className={clsx(
                  'h-full rounded-full transition-all duration-500',
                  row.state === 'failed' && 'bg-danger',
                  row.state === 'skipped' && 'bg-unverified',
                  row.state === 'running' && 'bg-accent',
                  (row.state === 'done' || row.state === 'ok') && 'bg-observed',
                )}
                style={{ width: `${row.percent}%` }}
              />
            </div>
            {row.note && <p className="mt-0.5 text-2xs text-fg-dim">{row.note}</p>}
          </li>
        ))}
      </ul>
      {data && data.correlation_percent > 0 && (
        <div className="mt-2">
          <div className="flex items-center justify-between text-2xs">
            <span className="text-fg-muted">correlation</span>
            <span className="tabular-nums text-fg-dim">{data.correlation_percent}%</span>
          </div>
          <div className="mt-0.5 h-1 overflow-hidden rounded-full bg-ink-700">
            <div
              className="h-full rounded-full bg-correlated transition-all duration-500"
              style={{ width: `${data.correlation_percent}%` }}
            />
          </div>
        </div>
      )}
      {errors.length > 0 && (
        <ul className="mt-2 space-y-0.5">
          {errors.slice(-3).map((error) => (
            <li key={error} className="text-2xs text-danger/80">
              {error}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
