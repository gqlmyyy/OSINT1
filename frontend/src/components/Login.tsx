import { useState } from 'react';
import { Button, Input } from '@/components/ui';
import { useAuthStore } from '@/store/auth';

export function Login() {
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const login = useAuthStore((s) => s.login);
  const register = useAuthStore((s) => s.register);
  const error = useAuthStore((s) => s.error);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      if (mode === 'login') await login(username, password);
      else await register(email, username, password);
    } catch {
      // The store holds the message; the form stays put so it can be corrected.
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-screen place-items-center bg-ink-900 px-4">
      <form onSubmit={submit} className="w-full max-w-sm rounded border border-line bg-ink-850 p-6">
        <h1 className="text-base font-semibold text-fg">GraphIntel OSINT</h1>
        <p className="mb-5 mt-1 text-2xs text-fg-muted">
          Evidence-first investigation of public information.
        </p>

        <div className="space-y-3">
          {mode === 'register' && (
            <div>
              <label htmlFor="email" className="mb-1 block text-2xs text-fg-dim">
                Email
              </label>
              <Input
                id="email"
                type="email"
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                autoComplete="email"
              />
            </div>
          )}
          <div>
            <label htmlFor="username" className="mb-1 block text-2xs text-fg-dim">
              Username
            </label>
            <Input
              id="username"
              required
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              autoComplete="username"
            />
          </div>
          <div>
            <label htmlFor="password" className="mb-1 block text-2xs text-fg-dim">
              Password
            </label>
            <Input
              id="password"
              type="password"
              required
              minLength={mode === 'register' ? 12 : 1}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete={mode === 'register' ? 'new-password' : 'current-password'}
            />
            {mode === 'register' && (
              <p className="mt-1 text-2xs text-fg-dim">At least 12 characters.</p>
            )}
          </div>
        </div>

        {error && <p className="mt-3 text-2xs text-danger">{error}</p>}

        <Button variant="primary" type="submit" disabled={busy} className="mt-4 w-full py-1.5">
          {busy ? 'Please wait…' : mode === 'login' ? 'Sign in' : 'Create account'}
        </Button>

        <button
          type="button"
          onClick={() => setMode(mode === 'login' ? 'register' : 'login')}
          className="mt-3 w-full text-2xs text-fg-dim hover:text-accent"
        >
          {mode === 'login' ? 'No account yet? Create the first one' : 'Already have an account? Sign in'}
        </button>
      </form>
    </div>
  );
}
