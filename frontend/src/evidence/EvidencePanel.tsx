import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '@/api/client';
import { AssertionBadge, Empty, ExternalLink, Panel, formatDate } from '@/components/ui';
import { useInvestigationStore } from '@/store/investigation';

/** Flat, filterable evidence feed for the whole investigation. */
export function EvidencePanel({ investigationId }: { investigationId: string }) {
  const [provider, setProvider] = useState<string>('');
  const selectNode = useInvestigationStore((s) => s.selectNode);

  const { data } = useQuery({
    queryKey: ['observations', investigationId, provider],
    queryFn: () => api.observations(investigationId, { provider: provider || undefined, limit: 200 }),
  });

  const providers = Array.from(new Set((data?.items ?? []).map((o) => o.provider))).sort();

  return (
    <Panel
      title={`Evidence${data ? ` — ${data.total}` : ''}`}
      actions={
        <select
          value={provider}
          onChange={(event) => setProvider(event.target.value)}
          aria-label="Filter evidence by provider"
          className="rounded border border-line bg-ink-900 px-1 py-0.5 text-2xs text-fg-muted outline-none focus:border-accent"
        >
          <option value="">all providers</option>
          {providers.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
      }
    >
      {!data?.items.length ? (
        <Empty>No evidence collected yet. Run a scan or seed the demo investigation.</Empty>
      ) : (
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-ink-850">
            <tr className="border-b border-line text-2xs uppercase tracking-wider text-fg-dim">
              <th className="px-3 py-1.5 text-left font-medium">Provider</th>
              <th className="px-2 py-1.5 text-left font-medium">Kind</th>
              <th className="px-2 py-1.5 text-left font-medium">Source</th>
              <th className="px-2 py-1.5 text-left font-medium">Observed</th>
              <th className="px-3 py-1.5 text-left font-medium">Class</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line/50">
            {data.items.map((observation) => (
              <tr
                key={observation.id}
                onClick={() => observation.entity_id && selectNode(observation.entity_id)}
                className="cursor-pointer transition hover:bg-ink-800"
              >
                <td className="px-3 py-1.5 text-fg">{observation.provider}</td>
                <td className="px-2 py-1.5 text-fg-muted">{observation.kind.replace(/_/g, ' ')}</td>
                <td className="max-w-[22rem] truncate px-2 py-1.5">
                  {observation.url ? (
                    <ExternalLink href={observation.url} />
                  ) : (
                    <span className="text-fg-dim">—</span>
                  )}
                </td>
                <td className="whitespace-nowrap px-2 py-1.5 tabular-nums text-fg-dim">
                  {formatDate(observation.observed_at)}
                </td>
                <td className="px-3 py-1.5">
                  <AssertionBadge assertion={observation.assertion} short />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}
