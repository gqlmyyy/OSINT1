import { Link, useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api/client';
import { Empty, SkeletonListRow, timeAgo } from '@/components/ui';
import { NewInvestigationWizard } from './NewInvestigationWizard';

export function InvestigationList() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ['investigations'],
    queryFn: () => api.listInvestigations({ limit: 50 }),
  });

  const seedDemo = useMutation({
    mutationFn: api.seedDemo,
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ['investigations'] });
      navigate(`/investigations/${result.investigation_id}`);
    },
  });

  return (
    <div className="mx-auto max-w-4xl px-6 py-8">
      <header className="mb-6">
        <h1 className="text-lg font-semibold text-fg">Investigations</h1>
        <p className="mt-1 text-xs text-fg-muted">
          Each investigation collects public information about the identifiers you supply and
          records where every finding came from.
        </p>
      </header>

      <NewInvestigationWizard />

      <p className="mb-8 mt-3 text-2xs text-fg-dim">
        No internet access?{' '}
        <button
          className="text-accent hover:underline"
          onClick={() => seedDemo.mutate()}
          disabled={seedDemo.isPending}
        >
          Seed the offline demo investigation
        </button>{' '}
        — synthetic data, no requests leave the host.
      </p>

      {isLoading ? (
        <div className="divide-y divide-line rounded border border-line bg-ink-850">
          {[0, 1, 2, 3].map((row) => (
            <SkeletonListRow key={row} />
          ))}
        </div>
      ) : !data?.items.length ? (
        <Empty>No investigations yet.</Empty>
      ) : (
        <ul className="divide-y divide-line rounded border border-line bg-ink-850">
          {data.items.map((investigation) => (
            <li key={investigation.id}>
              <Link
                to={`/investigations/${investigation.id}`}
                className="flex items-center gap-4 px-4 py-3 transition hover:bg-ink-800"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm text-fg">{investigation.name}</p>
                  <p className="truncate text-2xs text-fg-dim">
                    {investigation.targets.map((t) => t.value).join(', ') || 'no targets'}
                  </p>
                </div>
                <dl className="hidden shrink-0 gap-4 text-2xs text-fg-muted sm:flex">
                  <Metric label="entities" value={investigation.stats.entities} />
                  <Metric label="links" value={investigation.stats.relationships} />
                  <Metric label="matches" value={investigation.stats.strong_correlations} />
                </dl>
                <span className="w-20 shrink-0 text-right text-2xs text-fg-dim">
                  {timeAgo(investigation.updated_at)}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="text-center">
      <dd className="tabular-nums text-fg">{value}</dd>
      <dt className="text-fg-dim">{label}</dt>
    </div>
  );
}
