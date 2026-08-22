import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/api/client';
import { useAuthStore } from '@/store/auth';

interface Action {
  id: string;
  label: string;
  hint?: string;
  run: () => void;
}

/** Cmd/Ctrl+K anywhere in the app. Jump to an investigation or run a global action
 * without leaving the keyboard — the brief's "situation room" requirement. */
export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();
  const logout = useAuthStore((s) => s.logout);

  const { data: investigations } = useQuery({
    queryKey: ['command-palette-investigations'],
    queryFn: () => api.listInvestigations({ limit: 50 }),
    enabled: open,
    staleTime: 30_000,
  });

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const isPaletteShortcut = (event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k';
      if (isPaletteShortcut) {
        event.preventDefault();
        setOpen((wasOpen) => !wasOpen);
        return;
      }
      if (event.key === 'Escape') setOpen(false);
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  useEffect(() => {
    if (open) {
      setQuery('');
      // Wait a frame so the input exists before focusing.
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  const actions = useMemo<Action[]>(() => {
    const staticActions: Action[] = [
      { id: 'go-home', label: 'Go to all investigations', run: () => navigate('/') },
      { id: 'sign-out', label: 'Sign out', run: () => logout() },
    ];
    const investigationActions: Action[] = (investigations?.items ?? []).map((investigation) => ({
      id: `investigation:${investigation.id}`,
      label: investigation.name,
      hint: investigation.status,
      run: () => navigate(`/investigations/${investigation.id}`),
    }));
    const all = [...staticActions, ...investigationActions];
    if (!query.trim()) return all;
    const needle = query.trim().toLowerCase();
    return all.filter((action) => action.label.toLowerCase().includes(needle));
  }, [investigations, query, navigate, logout]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 pt-[15vh]"
      onClick={() => setOpen(false)}
      role="presentation"
    >
      <div
        className="w-full max-w-lg overflow-hidden rounded-lg border border-line bg-ink-850 shadow-2xl"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
      >
        <input
          ref={inputRef}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Jump to an investigation, or run a command…"
          className="w-full border-b border-line bg-transparent px-4 py-3 text-sm text-fg outline-none placeholder:text-fg-dim"
        />
        <ul className="max-h-80 overflow-y-auto py-1">
          {actions.length === 0 && (
            <li className="px-4 py-3 text-xs text-fg-dim">No matches.</li>
          )}
          {actions.map((action) => (
            <li key={action.id}>
              <button
                className="flex w-full items-center justify-between gap-2 px-4 py-2 text-left text-xs text-fg hover:bg-ink-800"
                onClick={() => {
                  action.run();
                  setOpen(false);
                }}
              >
                <span className="truncate">{action.label}</span>
                {action.hint && <span className="shrink-0 text-2xs text-fg-dim">{action.hint}</span>}
              </button>
            </li>
          ))}
        </ul>
        <div className="border-t border-line px-4 py-1.5 text-2xs text-fg-dim">
          <kbd className="rounded border border-line px-1">Esc</kbd> to close ·{' '}
          <kbd className="rounded border border-line px-1">⌘K</kbd> / <kbd className="rounded border border-line px-1">Ctrl K</kbd> to toggle
        </div>
      </div>
    </div>
  );
}
