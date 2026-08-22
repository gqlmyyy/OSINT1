import { useEffect } from 'react';
import { Link, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { CommandPalette } from '@/components/CommandPalette';
import { Login } from '@/components/Login';
import { ProviderHealthStrip } from '@/components/ProviderHealthStrip';
import { InvestigationList } from '@/investigations/InvestigationList';
import { Workspace } from '@/investigations/Workspace';
import { useAuthStore } from '@/store/auth';

export function App() {
  const user = useAuthStore((s) => s.user);
  const loading = useAuthStore((s) => s.loading);
  const bootstrap = useAuthStore((s) => s.bootstrap);
  const logout = useAuthStore((s) => s.logout);
  const location = useLocation();

  useEffect(() => {
    void bootstrap();
  }, [bootstrap]);

  if (loading) {
    return <div className="grid h-full place-items-center text-xs text-fg-dim">Starting…</div>;
  }
  if (!user) return <Login />;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex shrink-0 items-center gap-4 border-b border-line bg-ink-850 px-4 py-2">
        <Link to="/" className="flex items-center gap-2">
          <span className="grid h-5 w-5 place-items-center rounded-sm bg-accent/20 text-2xs font-bold text-accent">
            G
          </span>
          <span className="text-xs font-semibold tracking-tight text-fg">GraphIntel OSINT</span>
        </Link>
        {location.pathname !== '/' && (
          <Link to="/" className="text-2xs text-fg-dim hover:text-accent">
            ← all investigations
          </Link>
        )}
        <div className="ml-auto flex items-center gap-3 text-2xs text-fg-dim">
          <ProviderHealthStrip />
          <button
            onClick={() =>
              window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true }))
            }
            className="rounded border border-line px-1.5 py-0.5 hover:border-line-bright hover:text-fg"
            title="Command palette"
          >
            <kbd className="tabular-nums">⌘K</kbd>
          </button>
          <span>
            {user.username} · {user.role}
          </span>
          <button onClick={logout} className="hover:text-accent">
            Sign out
          </button>
        </div>
      </header>

      <div className="min-h-0 flex-1">
        <Routes>
          <Route path="/" element={<InvestigationList />} />
          <Route path="/investigations/:id" element={<Workspace />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </div>
      <CommandPalette />
    </div>
  );
}
