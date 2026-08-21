import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api/client';
import { Button, Empty, Input, timeAgo } from '@/components/ui';

export function InvestigationList() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [name, setName] = useState('');
  const [targets, setTargets] = useState('');
  const [error, setError] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['investigations'],
    queryFn: () => api.listInvestigations({ limit: 50 }),
  });

  const create = useMutation({
    mutationFn: () =>
      api.createInvestigation({
        name: name.trim(),
        targets: targets
          .split(/[\n,]/)
          .map((value) => value.trim())
          .filter(Boolean)
          .map((value) => ({ value })),
      }),
    onSuccess: (investigation) => {
      void queryClient.invalidateQueries({ queryKey: ['investigations'] });
      navigate(`/investigations/${investigation.id}`);
    },
    onError: (err: Error) => setError(err.message),
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

      <section className="mb-8 rounded border border-line bg-ink-850 p-4">
        <h2 className="mb-3 text-2xs font-semibold uppercase tracking-wider text-fg-dim">
          New investigation
        </h2>
        <div className="grid gap-3 sm:grid-cols-[1fr_1.4fr_auto] sm:items-start">
          <div>
            <label htmlFor="inv-name" className="mb-1 block text-2xs text-fg-dim">
              Name
            </label>
            <Input
              id="inv-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="example_user research"
            />
          </div>
          <div>
            <label htmlFor="inv-targets" className="mb-1 block text-2xs text-fg-dim">
              Targets — username, email, domain, URL, IP or phone, one per line
            </label>
            <textarea
              id="inv-targets"
              value={targets}
              onChange={(event) => setTargets(event.target.value)}
              rows={3}
              placeholder={'example_user\nexample@example.com\nexample.com'}
              className="w-full rounded border border-line bg-ink-900 px-2 py-1.5 font-mono text-xs text-fg outline-none focus:border-accent"
            />
          </div>
          <div className="flex gap-2 sm:mt-5">
            <Button
              variant="primary"
              disabled={!name.trim() || create.isPending}
              onClick={() => {
                setError(null);
                create.mutate();
              }}
            >
              Create
            </Button>
          </div>
        </div>
        {error && <p className="mt-2 text-2xs text-danger">{error}</p>}
        <p className="mt-3 text-2xs text-fg-dim">
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
      </section>

      {isLoading ? (
        <Empty>Loading…</Empty>
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
