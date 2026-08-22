import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, ApiError } from '@/api/client';
import type {
  ExposureCorrelation,
  ExposureFinding,
  LinkedAccount,
  SelfOsintEvidence,
  SelfOsintReport,
} from '@/api/types';
import { Button, Empty, ExternalLink, Skeleton, formatDate, timeAgo } from '@/components/ui';
import { AvailabilityBadge, CATEGORY_LABEL, SeverityBadge, TokenStateBadge } from './badges';

/**
 * Self-OSINT: a privacy audit of an account you own.
 *
 * The page is written for someone who is not an analyst. It answers, in order: what is
 * connected, what that connection can and cannot see, what was found, how exposed that
 * makes you, and what to do about it. Technical provenance stays available on every
 * finding — but behind the plain-language summary, not in front of it.
 */
export function SelfOsintInstagram() {
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const account = useQuery({
    queryKey: ['self-instagram-account'],
    queryFn: api.selfInstagramAccount,
  });
  const connected = account.data?.connected ?? false;

  const report = useQuery({
    queryKey: ['self-instagram-report'],
    queryFn: api.selfInstagramReport,
    enabled: connected,
    retry: false,
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['self-instagram-account'] });
    void queryClient.invalidateQueries({ queryKey: ['self-instagram-report'] });
  };

  const connect = useMutation({
    mutationFn: api.selfInstagramConnect,
    onSuccess: (result) => {
      // Leaves the SPA for Meta's own consent screen. Nothing is stored until the
      // user comes back with a code that matches the state we issued.
      window.location.assign(result.authorize_url);
    },
    onError: (err: unknown) => setError(describe(err)),
  });

  const completeCallback = useMutation({
    mutationFn: api.selfInstagramCallback,
    onSuccess: () => {
      setNotice('Instagram connected. Running the first analysis…');
      invalidate();
      sync.mutate(false);
    },
    onError: (err: unknown) => setError(describe(err)),
  });

  const sync = useMutation({
    mutationFn: (force: boolean) => api.selfInstagramSync(force),
    onSuccess: (result) => {
      setNotice(
        `Analysis complete: ${result.observations} observation(s) from ` +
          `${result.external_providers_run} provider run(s).`,
      );
      invalidate();
    },
    onError: (err: unknown) => setError(describe(err)),
  });

  const disconnect = useMutation({
    mutationFn: api.selfInstagramDisconnect,
    onSuccess: (result) => {
      setNotice(result.instructions);
      invalidate();
    },
    onError: (err: unknown) => setError(describe(err)),
  });

  const deleteData = useMutation({
    mutationFn: api.selfInstagramDeleteData,
    onSuccess: (result) => {
      const removed = Object.entries(result.removed)
        .map(([key, value]) => `${value} ${key}`)
        .join(', ');
      setNotice(`Deleted: ${removed || 'nothing was stored'}.`);
      invalidate();
    },
    onError: (err: unknown) => setError(describe(err)),
  });

  // Meta redirects back here with ?code&state. Complete the exchange authenticated,
  // then strip the parameters so a refresh cannot replay them.
  useEffect(() => {
    const code = params.get('code');
    const state = params.get('state');
    const oauthError = params.get('error_description') ?? params.get('error');
    if (oauthError) {
      setError(`Instagram did not grant access: ${oauthError}`);
      setParams({}, { replace: true });
      return;
    }
    if (code && state && completeCallback.isIdle) {
      completeCallback.mutate({ code, state });
      setParams({}, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

  if (account.isLoading) {
    return (
      <Page>
        <div className="space-y-3">
          <Skeleton className="h-6 w-64" />
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      </Page>
    );
  }

  return (
    <Page>
      <header className="mb-6">
        <h1 className="text-lg font-semibold text-fg">Your Instagram account</h1>
        <p className="mt-1 max-w-3xl text-xs leading-relaxed text-fg-muted">
          Connect an Instagram account <strong>you own or manage</strong> to see what you are
          publicly exposing. This reads your own account through Meta&apos;s official API and
          then runs the same public-source checks a stranger could run against the
          identifiers it finds. It gives no access to anyone else&apos;s account.
        </p>
      </header>

      {notice && <Banner tone="info" onDismiss={() => setNotice(null)}>{notice}</Banner>}
      {error && <Banner tone="error" onDismiss={() => setError(null)}>{error}</Banner>}

      <Section title="1. Account">
        <AccountSection
          account={account.data}
          onConnect={() => connect.mutate()}
          onSync={() => sync.mutate(true)}
          onDisconnect={() => disconnect.mutate()}
          onDelete={() => deleteData.mutate()}
          busy={
            connect.isPending ||
            sync.isPending ||
            completeCallback.isPending ||
            disconnect.isPending ||
            deleteData.isPending
          }
        />
      </Section>

      {connected && (
        <Section
          title="2. What this connection can and cannot see"
          hint="Not everything about your account is available through the official API. Anything marked NOT AVAILABLE is a limit of the lawful route — we do not scrape around it."
        >
          <ul className="divide-y divide-line/60">
            {(account.data?.capabilities ?? []).map((capability) => (
              <li key={capability.key} className="flex items-start gap-3 px-3 py-2">
                <span className="w-40 shrink-0">
                  <AvailabilityBadge value={capability.availability} />
                </span>
                <span className="min-w-0">
                  <p className="text-xs text-fg">{capability.label}</p>
                  <p className="mt-0.5 text-2xs leading-relaxed text-fg-dim">{capability.note}</p>
                </span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {connected && <ReportSections report={report.data} loading={report.isLoading} />}
    </Page>
  );
}

function ReportSections({
  report,
  loading,
}: {
  report: SelfOsintReport | undefined;
  loading: boolean;
}) {
  const byCategory = useMemo(() => {
    const groups = new Map<string, ExposureFinding[]>();
    for (const finding of report?.findings ?? []) {
      groups.set(finding.category, [...(groups.get(finding.category) ?? []), finding]);
    }
    return groups;
  }, [report]);

  if (loading) {
    return (
      <Section title="3. Findings">
        <div className="space-y-2 p-3">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-full" />
        </div>
      </Section>
    );
  }
  if (!report) return null;

  const pick = (...categories: string[]) =>
    categories.flatMap((category) => byCategory.get(category) ?? []);

  return (
    <>
      <Section title="3. Profile exposure">
        <FindingList findings={pick('identity_exposure', 'contact_exposure', 'location_exposure', 'organization_exposure')} />
      </Section>

      <Section title="4. Media exposure">
        <FindingList findings={pick('public_media_exposure', 'metadata_exposure')} />
      </Section>

      <Section title="5. Interaction exposure">
        <FindingList findings={pick('external_mentions')} />
      </Section>

      <Section
        title="6. Identity correlation"
        hint="How easily your accounts can be linked together. A shared username is a lead, never a proof of identity — this section says which is which."
      >
        <CorrelationList correlations={report.correlations} />
      </Section>

      <Section title="7. External discoveries">
        <FindingList findings={pick('username_reuse', 'external_account_linkage')} />
      </Section>

      <Section
        title="8. How exposed am I?"
        hint="Every point below traces to a specific finding. Nothing here is estimated."
      >
        <ScorePanel report={report} />
      </Section>

      <Section title="9. Evidence">
        <EvidenceTable findings={report.findings} />
      </Section>
    </>
  );
}

function AccountSection({
  account,
  onConnect,
  onSync,
  onDisconnect,
  onDelete,
  busy,
}: {
  account: LinkedAccount | undefined;
  onConnect: () => void;
  onSync: () => void;
  onDisconnect: () => void;
  onDelete: () => void;
  busy: boolean;
}) {
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  if (!account?.oauth_configured) {
    return (
      <div className="px-3 py-4">
        <p className="text-xs text-fg">
          Instagram is not configured on this deployment.
        </p>
        <p className="mt-1 text-2xs leading-relaxed text-fg-dim">
          An administrator needs to create a Meta app with the &ldquo;Instagram API with
          Instagram Login&rdquo; product and set <code>INSTAGRAM_APP_ID</code> and{' '}
          <code>INSTAGRAM_APP_SECRET</code>. See the README section
          &ldquo;Self-OSINT / Instagram account&rdquo;.
        </p>
      </div>
    );
  }

  if (!account.connected) {
    return (
      <div className="px-3 py-4">
        <p className="text-xs text-fg">No Instagram account is connected.</p>
        <p className="mt-1 max-w-2xl text-2xs leading-relaxed text-fg-dim">
          You will be sent to Instagram to sign in and approve access. This application
          never sees your Instagram password. Requires a Business or Creator account —
          Meta&apos;s API does not support personal accounts.
        </p>
        <Button variant="primary" className="mt-3" onClick={onConnect} disabled={busy}>
          Add your Instagram account
        </Button>
      </div>
    );
  }

  return (
    <div className="px-3 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-fg">@{account.username ?? 'unknown'}</span>
        {account.token_state && <TokenStateBadge value={account.token_state} />}
        {account.account_type && (
          <span className="text-2xs uppercase tracking-wider text-fg-dim">
            {account.account_type}
          </span>
        )}
      </div>
      {account.token_state_detail && (
        <p className="mt-1.5 text-2xs leading-relaxed text-inferred">
          {account.token_state_detail}
        </p>
      )}

      <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1 text-2xs sm:grid-cols-3">
        <Field label="Last analysed">
          {account.last_synced_at ? timeAgo(account.last_synced_at) : 'never'}
        </Field>
        <Field label="Authorisation expires">
          {account.expires_at ? formatDate(account.expires_at) : 'unknown'}
        </Field>
        <Field label="Permissions granted">{account.scopes.join(', ') || 'none'}</Field>
      </dl>

      <div className="mt-4 flex flex-wrap gap-2">
        <Button onClick={onSync} disabled={busy}>
          {busy ? 'Working…' : 'Re-run analysis'}
        </Button>
        <Button onClick={onDisconnect} disabled={busy}>
          Disconnect
        </Button>
        {confirmingDelete ? (
          <>
            <Button variant="danger" onClick={onDelete} disabled={busy}>
              Yes, delete everything
            </Button>
            <Button variant="ghost" onClick={() => setConfirmingDelete(false)}>
              Cancel
            </Button>
          </>
        ) : (
          <Button variant="danger" onClick={() => setConfirmingDelete(true)} disabled={busy}>
            Delete collected data
          </Button>
        )}
      </div>
      <p className="mt-2 max-w-2xl text-2xs leading-relaxed text-fg-dim">
        <strong>Disconnect</strong> destroys the stored credential and stops analysis, but
        keeps what has already been collected. <strong>Delete collected data</strong> also
        erases every finding, entity and observation from this audit. Security audit
        records of these actions are kept either way.
      </p>
      {account.revocation_instructions && (
        <p className="mt-2 max-w-2xl text-2xs leading-relaxed text-fg-dim">
          {account.revocation_instructions}
        </p>
      )}
    </div>
  );
}

function FindingList({ findings }: { findings: ExposureFinding[] }) {
  if (findings.length === 0) {
    return <Empty>Nothing found in this category.</Empty>;
  }
  return (
    <ul className="divide-y divide-line/60">
      {findings.map((finding) => (
        <FindingRow key={`${finding.category}:${finding.title}`} finding={finding} />
      ))}
    </ul>
  );
}

function FindingRow({ finding }: { finding: ExposureFinding }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="px-3 py-3">
      <div className="flex items-start gap-2">
        <SeverityBadge value={finding.severity} />
        <div className="min-w-0 flex-1">
          <p className="text-xs font-medium text-fg">{finding.title}</p>
          <p className="mt-1 text-2xs leading-relaxed text-fg-muted">{finding.description}</p>
          <p className="mt-2 text-2xs leading-relaxed text-observed">
            <span className="font-semibold">What to do: </span>
            {finding.mitigation}
          </p>
          <button
            onClick={() => setOpen((wasOpen) => !wasOpen)}
            className="mt-2 text-2xs text-accent hover:underline"
          >
            {open ? 'Hide evidence' : `Show evidence (${finding.evidence.length})`}
          </button>
          {open && <EvidenceList evidence={finding.evidence} confidence={finding.confidence} />}
        </div>
      </div>
    </li>
  );
}

function EvidenceList({
  evidence,
  confidence,
}: {
  evidence: SelfOsintEvidence[];
  confidence: number;
}) {
  return (
    <div className="mt-2 rounded border border-line bg-ink-900 p-2">
      <p className="mb-1.5 text-2xs text-fg-dim">
        Confidence <span className="tabular-nums text-fg-muted">{Math.round(confidence * 100)}%</span>
      </p>
      <ul className="space-y-1">
        {evidence.map((item, index) => (
          <li key={`${item.label}:${index}`} className="text-2xs">
            <span className="text-fg">{item.label}</span>
            <span className="text-fg-dim"> — source: {item.source}</span>
            {item.url && (
              <>
                {' '}
                <ExternalLink href={item.url}>open</ExternalLink>
              </>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function CorrelationList({ correlations }: { correlations: ExposureCorrelation[] }) {
  if (correlations.length === 0) {
    return <Empty>No cross-account correlations were found.</Empty>;
  }
  return (
    <ul className="divide-y divide-line/60">
      {correlations.map((correlation, index) => (
        <li key={`${correlation.signal}:${index}`} className="px-3 py-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-medium text-fg">{correlation.signal}</span>
            <span
              className={
                correlation.identity_claim
                  ? 'rounded border border-correlated/60 px-1.5 py-px text-2xs text-correlated'
                  : 'rounded border border-line px-1.5 py-px text-2xs text-fg-dim'
              }
            >
              {correlation.identity_claim ? 'identity signal' : 'correlation risk only'}
            </span>
            <span className="tabular-nums text-2xs text-fg-muted">
              {Math.round(correlation.confidence * 100)}%
            </span>
          </div>
          <p className="mt-1 text-2xs leading-relaxed text-fg-muted">{correlation.explanation}</p>
          <p className="mt-1 text-2xs text-fg-dim">Source: {correlation.source}</p>
          <EvidenceList evidence={correlation.evidence} confidence={correlation.confidence} />
        </li>
      ))}
    </ul>
  );
}

function ScorePanel({ report }: { report: SelfOsintReport }) {
  const { score } = report;
  const tone =
    score.level === 'high'
      ? 'text-danger'
      : score.level === 'moderate'
        ? 'text-inferred'
        : score.level === 'low'
          ? 'text-correlated'
          : 'text-fg-muted';

  return (
    <div className="px-3 py-3">
      <div className="flex items-baseline gap-3">
        <span className={`text-3xl font-semibold tabular-nums ${tone}`}>{score.overall}</span>
        <span className="text-xs uppercase tracking-wider text-fg-dim">
          {score.level} exposure
        </span>
      </div>
      <p className="mt-2 max-w-3xl text-2xs leading-relaxed text-fg-dim">{score.explanation}</p>

      {score.categories.length > 0 && (
        <ul className="mt-4 space-y-2">
          {score.categories.map((category) => (
            <li key={category.category}>
              <div className="flex items-center justify-between gap-2 text-2xs">
                <span className="text-fg">
                  {CATEGORY_LABEL[category.category] ?? category.category}
                </span>
                <span className="tabular-nums text-fg-muted">{category.score}</span>
              </div>
              <div className="mt-1 h-1 w-full overflow-hidden rounded-full bg-ink-700">
                <div
                  className="h-full rounded-full bg-accent"
                  style={{ width: `${Math.min(100, category.score)}%` }}
                />
              </div>
              <ul className="mt-1 space-y-0.5">
                {category.contributions.map((contribution, index) => (
                  <li key={`${contribution.title}:${index}`} className="text-2xs text-fg-dim">
                    +{contribution.points} — {contribution.title} ({contribution.severity},{' '}
                    {contribution.evidence_count} evidence item
                    {contribution.evidence_count === 1 ? '' : 's'})
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function EvidenceTable({ findings }: { findings: ExposureFinding[] }) {
  const rows = findings.flatMap((finding) =>
    finding.evidence.map((evidence) => ({ finding, evidence })),
  );
  if (rows.length === 0) return <Empty>No evidence has been collected yet.</Empty>;

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[40rem] text-2xs">
        <thead>
          <tr className="border-b border-line text-left text-fg-dim">
            <th className="px-3 py-1.5 font-medium">Finding</th>
            <th className="px-3 py-1.5 font-medium">Evidence</th>
            <th className="px-3 py-1.5 font-medium">Source</th>
            <th className="px-3 py-1.5 font-medium">Link</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ finding, evidence }, index) => (
            <tr key={`${finding.title}:${index}`} className="border-b border-line/50">
              <td className="px-3 py-1.5 text-fg-muted">{finding.title}</td>
              <td className="px-3 py-1.5 text-fg">{evidence.label}</td>
              <td className="px-3 py-1.5 text-fg-dim">{evidence.source}</td>
              <td className="px-3 py-1.5">
                {evidence.url ? <ExternalLink href={evidence.url}>open</ExternalLink> : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// -- layout helpers -----------------------------------------------------------

function Page({ children }: { children: React.ReactNode }) {
  return <div className="mx-auto max-w-4xl px-6 py-8">{children}</div>;
}

function Section({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mb-4 rounded border border-line bg-ink-850">
      <header className="border-b border-line px-3 py-2">
        <h2 className="text-2xs font-semibold uppercase tracking-wider text-fg-muted">{title}</h2>
        {hint && <p className="mt-1 text-2xs leading-relaxed text-fg-dim">{hint}</p>}
      </header>
      {children}
    </section>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-fg-dim">{label}</dt>
      <dd className="text-fg">{children}</dd>
    </div>
  );
}

function Banner({
  tone,
  children,
  onDismiss,
}: {
  tone: 'info' | 'error';
  children: React.ReactNode;
  onDismiss: () => void;
}) {
  return (
    <div
      className={
        tone === 'error'
          ? 'mb-4 flex items-start gap-3 rounded border border-danger/50 bg-danger/10 px-3 py-2'
          : 'mb-4 flex items-start gap-3 rounded border border-accent/40 bg-accent/10 px-3 py-2'
      }
    >
      <p
        className={`min-w-0 flex-1 text-2xs leading-relaxed ${
          tone === 'error' ? 'text-danger' : 'text-fg'
        }`}
      >
        {children}
      </p>
      <button onClick={onDismiss} className="shrink-0 text-2xs text-fg-dim hover:text-fg">
        dismiss
      </button>
    </div>
  );
}

function describe(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return error instanceof Error ? error.message : 'Something went wrong.';
}
