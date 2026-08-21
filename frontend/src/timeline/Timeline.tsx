import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';
import clsx from 'clsx';
import { api } from '@/api/client';
import { Empty, ExternalLink, Panel, formatDate } from '@/components/ui';
import { useInvestigationStore } from '@/store/investigation';

const KIND_COLOR: Record<string, string> = {
  observation: 'bg-observed',
  relationship: 'bg-accent',
  correlation: 'bg-correlated',
};

/**
 * Event feed plus the time slider that scopes the graph to a window (spec 16).
 * Moving the slider filters the graph rather than re-querying, so it stays instant.
 */
export function Timeline({ investigationId }: { investigationId: string }) {
  const selectedEntity = useInvestigationStore((s) => s.selectedNodeId);
  const until = useInvestigationStore((s) => s.filters.until);
  const setFilter = useInvestigationStore((s) => s.setFilter);
  const selectNode = useInvestigationStore((s) => s.selectNode);

  const { data } = useQuery({
    queryKey: ['timeline', investigationId, selectedEntity],
    queryFn: () => api.timeline(investigationId, selectedEntity ?? undefined),
  });

  const bounds = useMemo(() => {
    if (!data?.length) return null;
    const times = data.map((event) => Date.parse(event.at));
    return { min: Math.min(...times), max: Math.max(...times) };
  }, [data]);

  const sliderValue = until && bounds ? Date.parse(until) : bounds?.max ?? 0;

  return (
    <Panel
      title={selectedEntity ? 'Timeline — selected entity' : 'Timeline'}
      actions={
        until && (
          <button
            onClick={() => setFilter('until', null)}
            className="text-2xs text-accent hover:underline"
          >
            clear time filter
          </button>
        )
      }
    >
      {bounds && (
        <div className="border-b border-line px-3 py-2">
          <input
            type="range"
            min={bounds.min}
            max={bounds.max}
            value={sliderValue}
            onChange={(event) =>
              setFilter('until', new Date(Number(event.target.value)).toISOString())
            }
            className="w-full accent-accent"
            aria-label="Show the graph as it stood up to this point in time"
          />
          <div className="flex justify-between text-2xs text-fg-dim">
            <span>{formatDate(new Date(bounds.min).toISOString())}</span>
            <span className={clsx(until && 'text-accent')}>
              {formatDate(new Date(sliderValue).toISOString())}
            </span>
            <span>{formatDate(new Date(bounds.max).toISOString())}</span>
          </div>
        </div>
      )}

      {!data?.length ? (
        <Empty>Nothing recorded yet.</Empty>
      ) : (
        <ol className="relative space-y-0">
          {data.map((event, index) => (
            <li
              key={`${event.at}-${index}`}
              className="relative flex gap-2 border-b border-line/50 px-3 py-1.5 last:border-b-0"
            >
              <span
                className={clsx(
                  'mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full',
                  KIND_COLOR[event.kind] ?? 'bg-unverified',
                )}
              />
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline justify-between gap-2">
                  <button
                    className="truncate text-left text-xs text-fg hover:text-accent"
                    onClick={() => event.entity_id && selectNode(event.entity_id)}
                  >
                    {event.label}
                  </button>
                  <span className="shrink-0 tabular-nums text-2xs text-fg-dim">
                    {formatDate(event.at)}
                  </span>
                </div>
                <p className="truncate text-2xs text-fg-muted">{event.description}</p>
                {event.url && (
                  <p className="truncate text-2xs">
                    <ExternalLink href={event.url} />
                  </p>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
    </Panel>
  );
}
