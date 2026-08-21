import { useQuery } from '@tanstack/react-query';
import clsx from 'clsx';
import { api } from '@/api/client';
import type { Finding } from '@/api/types';
import { Empty, Panel } from '@/components/ui';
import { useInvestigationStore } from '@/store/investigation';

/**
 * What the investigation actually found, in the order worth reading.
 *
 * A finished investigation holds hundreds of rows; showing them all is the same as
 * showing nothing. This panel leads with conclusions in plain language and keeps the
 * two kinds of claim visibly apart — an identity correlation and a record of public
 * activity are different statements, and conflating them is how these tools mislead.
 */

const SEVERITY: Record<Finding['severity'], { label: string; className: string; mark: string }> = {
  strong: { label: 'Strong', className: 'border-observed/60 text-observed', mark: '●●●' },
  probable: { label: 'Probable', className: 'border-correlated/60 text-correlated', mark: '●●○' },
  possible: { label: 'Possible', className: 'border-inferred/60 text-inferred', mark: '●○○' },
  informational: { label: 'Context', className: 'border-unverified/60 text-unverified', mark: '○○○' },
};

const KIND_LABEL: Record<Finding['kind'], string> = {
  identity: 'Possible same owner',
  interaction: 'Public interaction',
  pivot: 'Lead to follow',
  reach: 'Reach',
};

/** Plain words first; the technical name stays available in Advanced details. */
const SUMMARY_LABELS: Record<string, string> = {
  accounts: 'Accounts found',
  posts: 'Public posts',
  hashtags: 'Hashtags',
  domains: 'Domains',
  external_links: 'External links',
  emails: 'Emails',
  repositories: 'Repositories',
  public_interactions: 'Public interactions',
  interacting_accounts: 'Interacting accounts',
  strong_correlations: 'Strong correlations',
  possible_correlations: 'Possible matches',
  relationships: 'Relationships',
  sources: 'Sources',
};

const SUMMARY_ORDER = [
  'accounts',
  'posts',
  'public_interactions',
  'interacting_accounts',
  'domains',
  'external_links',
  'hashtags',
  'strong_correlations',
  'possible_correlations',
  'relationships',
  'sources',
];

export function Findings({ investigationId }: { investigationId: string }) {
  const selectNode = useInvestigationStore((s) => s.selectNode);
  const { data, isLoading } = useQuery({
    queryKey: ['findings', investigationId],
    queryFn: () => api.findings(investigationId),
  });

  if (isLoading) return <Panel title="What we found"><Empty>Working…</Empty></Panel>;
  if (!data) return <Panel title="What we found"><Empty>Nothing to show yet.</Empty></Panel>;

  const cards = SUMMARY_ORDER.filter((key) => (data.summary[key] ?? 0) > 0);

  return (
    <Panel title="What we found">
      {cards.length > 0 && (
        <div className="grid grid-cols-2 gap-2 border-b border-line p-3 sm:grid-cols-3 lg:grid-cols-4">
          {cards.map((key) => (
            <div key={key} className="rounded border border-line bg-ink-800 px-2 py-1.5">
              <p className="text-base font-semibold tabular-nums text-fg">{data.summary[key]}</p>
              <p className="text-2xs leading-tight text-fg-dim">{SUMMARY_LABELS[key] ?? key}</p>
            </div>
          ))}
        </div>
      )}

      {data.findings.length === 0 ? (
        <Empty>
          No findings yet. Run a scan, or seed the demo investigation to see how this looks.
        </Empty>
      ) : (
        <ul className="divide-y divide-line/60">
          {data.findings.map((finding, index) => {
            const severity = SEVERITY[finding.severity];
            return (
              <li key={`${finding.kind}-${index}`} className="px-3 py-2.5">
                <div className="flex items-start gap-2">
                  <span
                    className={clsx(
                      'mt-0.5 shrink-0 rounded-full border px-1.5 py-px font-mono text-2xs',
                      severity.className,
                    )}
                    // Never colour alone: the mark and the word carry the same meaning.
                    title={`${severity.label} — ${KIND_LABEL[finding.kind]}`}
                  >
                    {severity.mark}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-xs font-medium text-fg">{finding.headline}</p>
                    <p className="mt-0.5 text-2xs leading-relaxed text-fg-muted">
                      {finding.detail}
                    </p>
                    <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1">
                      <span className={clsx('text-2xs', severity.className.split(' ')[1])}>
                        {severity.label} · {KIND_LABEL[finding.kind]}
                      </span>
                      {Object.entries(finding.metrics).map(([key, value]) => (
                        <span key={key} className="text-2xs text-fg-dim">
                          {key.replace(/_/g, ' ')}: <span className="tabular-nums">{value}</span>
                        </span>
                      ))}
                      {finding.entity_ids.slice(0, 2).map((id) => (
                        <button
                          key={id}
                          onClick={() => selectNode(id)}
                          className="text-2xs text-accent hover:underline"
                        >
                          view evidence
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}

/** Account-to-account public activity. Deliberately never labelled as identity. */
export function Interactions({ investigationId }: { investigationId: string }) {
  const selectNode = useInvestigationStore((s) => s.selectNode);
  const { data } = useQuery({
    queryKey: ['interactions', investigationId],
    queryFn: () => api.interactions(investigationId),
  });

  if (!data?.length) {
    return (
      <Panel title="Public interactions">
        <Empty>No public interactions recorded between accounts in this investigation.</Empty>
      </Panel>
    );
  }

  return (
    <Panel title={`Public interactions — ${data.length}`}>
      <p className="border-b border-line px-3 py-2 text-2xs text-fg-dim">
        How often these accounts publicly interact. This counts activity; it says nothing
        about whether two accounts belong to the same person.
      </p>
      <ul className="divide-y divide-line/60">
        {data.map((row) => (
          <li key={`${row.actor_id}-${row.target_id}`} className="px-3 py-2">
            <div className="flex items-center justify-between gap-2">
              <button
                className="truncate text-xs text-fg hover:text-accent"
                onClick={() => selectNode(row.actor_id)}
              >
                {row.actor_label}
              </button>
              <span className="shrink-0 tabular-nums text-2xs text-fg-muted">
                {row.total} interaction{row.total === 1 ? '' : 's'}
              </span>
            </div>
            <button
              className="truncate text-2xs text-fg-dim hover:text-accent"
              onClick={() => selectNode(row.target_id)}
            >
              → {row.target_label}
            </button>
            <p className="mt-0.5 text-2xs text-fg-muted">{row.description}</p>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
