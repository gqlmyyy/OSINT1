import { useState } from 'react';
import { Button, Input } from '@/components/ui';
import { useAuthStore } from '@/store/auth';

// These mirror RegisterRequest in backend/app/schemas/auth.py. They are stated here so
// the form can reject bad input before a round trip; the server remains the authority
// and its per-field errors are surfaced verbatim when it disagrees.
export const USERNAME_MIN_LENGTH = 3;
// The server's rule is `^[A-Za-z0-9._-]+$`. The hyphen is escaped here because browsers
// compile the `pattern` attribute with the RegExp `v` flag, where a trailing `-` in a
// character class is a syntax error — and an uncompilable pattern is silently ignored,
// which would leave this field with no constraint at all.
export const USERNAME_PATTERN = '[A-Za-z0-9._\\-]+';
export const PASSWORD_MIN_LENGTH = 12;
export const PASSWORD_MIN_DISTINCT = 5;

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
              // Mirrors RegisterRequest.username on the server, so an invalid handle is
              // caught here instead of coming back as a 422.
              {...(mode === 'register'
                ? {
                    minLength: USERNAME_MIN_LENGTH,
                    maxLength: 64,
                    pattern: USERNAME_PATTERN,
                    title: 'Letters, digits, dot, underscore or hyphen only.',
                  }
                : {})}
            />
            {mode === 'register' && (
              <p className="mt-1 text-2xs text-fg-dim">
                At least {USERNAME_MIN_LENGTH} characters: letters, digits, dot,
                underscore or hyphen.
              </p>
            )}
          </div>
          <div>
            <label htmlFor="password" className="mb-1 block text-2xs text-fg-dim">
              Password
            </label>
            <Input
              id="password"
              type="password"
              required
              minLength={mode === 'register' ? PASSWORD_MIN_LENGTH : 1}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete={mode === 'register' ? 'new-password' : 'current-password'}
            />
            {mode === 'register' && (
              <p className="mt-1 text-2xs text-fg-dim">
                At least {PASSWORD_MIN_LENGTH} characters, using at least{' '}
                {PASSWORD_MIN_DISTINCT} different ones.
              </p>
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
