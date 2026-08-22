import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import clsx from 'clsx';
import { api } from '@/api/client';
import type { Source } from '@/api/types';

const DOT: Record<Source['health'], string> = {
  ok: 'bg-observed',
  degraded: 'bg-inferred',
  unavailable: 'bg-fg-dim',
  disabled: 'bg-fg-dim',
};

/** Always visible, not just on failure — the brief's persistent provider-health
 * requirement. A quiet strip in the app header, one dot per registered source. */
export function ProviderHealthStrip() {
  const [open, setOpen] = useState(false);
  const { data } = useQuery({
    queryKey: ['sources'],
    queryFn: api.sources,
    refetchInterval: 60_000,
    staleTime: 30_000,
  });

  useEffect(() => {
    if (!open) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') setOpen(false);
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [open]);

  if (!data || data.length === 0) return null;

  const okCount = data.filter((source) => source.health === 'ok').length;
  const attentionNeeded = data.filter((source) => source.health !== 'ok');

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((wasOpen) => !wasOpen)}
        className="flex items-center gap-1.5 rounded border border-transparent px-1.5 py-1 text-2xs text-fg-dim hover:border-line hover:text-fg"
        title={`${okCount}/${data.length} sources available`}
      >
        <span className="flex items-center -space-x-0.5">
          {data.map((source) => (
            <span
              key={source.name}
              className={clsx('h-1.5 w-1.5 rounded-full ring-2 ring-ink-850', DOT[source.health])}
            />
          ))}
        </span>
        <span className="tabular-nums">
          {okCount}/{data.length}
        </span>
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} role="presentation" />
          <div className="absolute right-0 top-full z-50 mt-1 w-72 rounded-lg border border-line bg-ink-850 shadow-2xl">
            <div className="border-b border-line px-3 py-2 text-2xs font-semibold uppercase tracking-wider text-fg-muted">
              Sources
            </div>
            <ul className="max-h-72 overflow-y-auto py-1">
              {data.map((source) => (
                <li key={source.name} className="flex items-start gap-2 px-3 py-1.5 text-xs">
                  <span className={clsx('mt-1 h-1.5 w-1.5 shrink-0 rounded-full', DOT[source.health])} />
                  <div className="min-w-0">
                    <p className="truncate text-fg">{source.name}</p>
                    {source.health !== 'ok' && (
                      <p className="mt-0.5 text-2xs text-fg-dim">{source.health_detail || source.health}</p>
                    )}
                  </div>
                </li>
              ))}
            </ul>
            {attentionNeeded.length === 0 && (
              <p className="border-t border-line px-3 py-2 text-2xs text-fg-dim">All sources available.</p>
            )}
          </div>
        </>
      )}
    </div>
  );
}
