import clsx from 'clsx';
import type { ReactNode } from 'react';
import type { Assertion, MatchBand } from '@/api/types';

export function Panel({
  title,
  actions,
  children,
  className,
}: {
  title?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={clsx('flex min-h-0 flex-col bg-ink-850', className)}>
      {title && (
        <header className="flex shrink-0 items-center justify-between border-b border-line px-3 py-2">
          <h2 className="text-2xs font-semibold uppercase tracking-wider text-fg-muted">{title}</h2>
          {actions}
        </header>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
    </section>
  );
}

const ASSERTION_STYLE: Record<Assertion, string> = {
  observed: 'border-observed/60 text-observed',
  correlated: 'border-correlated/60 text-correlated',
  inferred: 'border-inferred/60 text-inferred',
  unverified: 'border-unverified/60 text-unverified',
};

const ASSERTION_LABEL: Record<Assertion, string> = {
  observed: 'Observed fact',
  inferred: 'Inference',
  correlated: 'Correlation',
  unverified: 'Unverified',
};

export function AssertionBadge({ assertion, short }: { assertion: Assertion; short?: boolean }) {
  return (
    <span
      className={clsx(
        'inline-block whitespace-nowrap rounded-full border px-1.5 py-px text-2xs',
        ASSERTION_STYLE[assertion],
      )}
      title={`${ASSERTION_LABEL[assertion]} — see the evidence before relying on it`}
    >
      {short ? assertion : ASSERTION_LABEL[assertion]}
    </span>
  );
}

const BAND_STYLE: Record<MatchBand, string> = {
  possible: 'border-unverified/60 text-unverified',
  probable: 'border-inferred/60 text-inferred',
  strong: 'border-correlated/60 text-correlated',
  confirmed: 'border-observed/60 text-observed',
};

export function BandBadge({ band, label }: { band: MatchBand; label: string }) {
  return (
    <span className={clsx('rounded-full border px-2 py-px text-2xs', BAND_STYLE[band])}>
      {label}
    </span>
  );
}

export function Confidence({
  value,
  rawValue,
  isStale,
  note,
  className,
}: {
  /** The number to render — pass display_confidence where one exists, so decay shows. */
  value: number;
  /** The stored (pre-decay) confidence, shown in the tooltip when it differs from value. */
  rawValue?: number;
  isStale?: boolean;
  note?: string | null;
  className?: string;
}) {
  const percent = Math.round(value * 100);
  const tone = percent >= 85 ? 'bg-observed' : percent >= 70 ? 'bg-correlated' : percent >= 50 ? 'bg-inferred' : 'bg-unverified';
  const title =
    note ?? (rawValue !== undefined && Math.round(rawValue * 100) !== percent
      ? `Recorded at ${Math.round(rawValue * 100)}% — shown reduced for age`
      : undefined);
  return (
    <span className={clsx('inline-flex items-center gap-1.5', className)} title={title}>
      <span className="h-1 w-12 overflow-hidden rounded-full bg-ink-700">
        <span className={clsx('block h-full rounded-full', tone)} style={{ width: `${percent}%` }} />
      </span>
      <span className="tabular-nums text-2xs text-fg-muted">{percent}%</span>
      {isStale && (
        <span className="inline-flex items-center gap-0.5 text-2xs text-fg-dim" aria-label="Needs recent verification">
          <svg width="10" height="10" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <circle cx="8" cy="8" r="6.5" stroke="currentColor" strokeWidth="1.3" />
            <path d="M8 4.5v4l2.6 1.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
          </svg>
          stale
        </span>
      )}
    </span>
  );
}

export function Button({
  children,
  variant = 'default',
  className,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'default' | 'primary' | 'ghost' | 'danger' }) {
  return (
    <button
      {...props}
      className={clsx(
        'rounded border px-2.5 py-1 text-xs transition disabled:cursor-not-allowed disabled:opacity-40',
        variant === 'primary' && 'border-accent bg-accent/15 text-accent hover:bg-accent/25',
        variant === 'default' && 'border-line bg-ink-800 text-fg hover:border-line-bright',
        variant === 'ghost' && 'border-transparent text-fg-muted hover:bg-ink-800 hover:text-fg',
        variant === 'danger' && 'border-danger/50 text-danger hover:bg-danger/10',
        className,
      )}
    >
      {children}
    </button>
  );
}

export function Input({ className, ...props }: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={clsx(
        'w-full rounded border border-line bg-ink-900 px-2 py-1.5 text-xs text-fg placeholder:text-fg-dim',
        'outline-none transition focus:border-accent',
        className,
      )}
    />
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="px-3 py-6 text-center text-xs text-fg-dim">{children}</p>;
}

/** A shimmering placeholder block, shaped like the content it stands in for — never a
 * bare "Loading…" string. `data-testid="skeleton"` so tests can assert it renders. */
export function Skeleton({ className }: { className?: string }) {
  return (
    <span
      data-testid="skeleton"
      aria-hidden="true"
      className={clsx('block animate-pulse rounded bg-ink-700', className)}
    />
  );
}

/** A row shaped like one investigation-list entry (label + metrics + timestamp). */
export function SkeletonListRow() {
  return (
    <div className="flex items-center gap-4 px-4 py-3">
      <div className="min-w-0 flex-1 space-y-1.5">
        <Skeleton className="h-3.5 w-40" />
        <Skeleton className="h-2.5 w-56" />
      </div>
      <Skeleton className="h-2.5 w-24" />
    </div>
  );
}

/** Shaped like the entity-inspector header (dot + label + confidence bar) plus a few
 * key/value rows, so the panel does not jump when the real data arrives. */
export function SkeletonEntityPanel() {
  return (
    <div>
      <div className="border-b border-line px-3 py-2">
        <div className="flex items-start gap-2">
          <Skeleton className="mt-1 h-2.5 w-2.5 shrink-0 rounded-full" />
          <div className="min-w-0 flex-1 space-y-1.5">
            <Skeleton className="h-3.5 w-32" />
            <Skeleton className="h-2 w-16" />
          </div>
        </div>
        <Skeleton className="mt-3 h-1 w-32" />
      </div>
      <div className="space-y-2 px-3 py-3">
        {[0, 1, 2, 3].map((row) => (
          <Skeleton key={row} className="h-2.5 w-full" />
        ))}
      </div>
    </div>
  );
}

export function KeyValue({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[6.5rem_1fr] gap-2 px-3 py-1 text-xs">
      <dt className="truncate text-fg-dim">{label}</dt>
      <dd className="min-w-0 break-words text-fg">{children}</dd>
    </div>
  );
}

export function ExternalLink({ href, children }: { href: string; children?: ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer noopener nofollow"
      className="break-all text-accent hover:underline"
    >
      {children ?? href}
    </a>
  );
}

export function timeAgo(iso: string | null): string {
  if (!iso) return '—';
  const delta = Date.now() - Date.parse(iso);
  const days = Math.floor(delta / 86_400_000);
  if (days > 365) return `${Math.floor(days / 365)}y ago`;
  if (days > 30) return `${Math.floor(days / 30)}mo ago`;
  if (days > 0) return `${days}d ago`;
  const hours = Math.floor(delta / 3_600_000);
  if (hours > 0) return `${hours}h ago`;
  return 'just now';
}

export function formatDate(iso: string | null): string {
  return iso ? new Date(iso).toISOString().slice(0, 16).replace('T', ' ') : '—';
}
