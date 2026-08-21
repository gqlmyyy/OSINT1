import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import clsx from 'clsx';
import { api } from '@/api/client';
import { Button, Input } from '@/components/ui';

/**
 * Three steps, plain language, technical choices behind "Advanced".
 *
 * The scope step is not decoration: each option maps to a set of providers, so an
 * investigator who only wants to check handles does not also fetch every post and
 * comment. Collecting less by default is both faster and the right default under the
 * project's data-minimisation rule.
 */

interface ScopeOption {
  id: string;
  label: string;
  hint: string;
  providers: string[];
  defaultOn: boolean;
}

const SCOPE: ScopeOption[] = [
  {
    id: 'social',
    label: 'Social accounts and public posts',
    hint: 'Profiles, posts, replies, mentions and hashtags where the platform publishes them',
    providers: ['mastodon', 'instagram'],
    defaultOn: true,
  },
  {
    id: 'cross_platform',
    label: 'The same handle on other platforms',
    hint: 'Checks public profile pages across many sites — a lead, not proof of one owner',
    providers: ['username_enum', 'github', 'maigret', 'sherlock'],
    defaultOn: true,
  },
  {
    id: 'web',
    label: 'Websites and domains',
    hint: 'Follows links from profiles, then DNS, registration data and certificates',
    providers: ['website', 'dns', 'whois', 'crtsh'],
    defaultOn: true,
  },
  {
    id: 'email',
    label: 'Publicly published email addresses',
    hint: 'Only addresses the target has published themselves; never guessed',
    providers: ['gravatar', 'holehe'],
    defaultOn: false,
  },
  {
    id: 'search',
    label: 'Search engine mentions',
    hint: 'Requires a search endpoint you are permitted to query',
    providers: ['search'],
    defaultOn: false,
  },
];

const EXAMPLES = ['example_user', 'https://www.instagram.com/example_user/', 'example.com'];

export function NewInvestigationWizard({ onCancel }: { onCancel?: () => void }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [step, setStep] = useState(1);
  const [name, setName] = useState('');
  const [targets, setTargets] = useState('');
  const [enabled, setEnabled] = useState<Set<string>>(
    () => new Set(SCOPE.filter((s) => s.defaultOn).map((s) => s.id)),
  );
  const [depth, setDepth] = useState(2);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { data: sources } = useQuery({ queryKey: ['sources'], queryFn: api.sources });

  const targetList = targets
    .split(/[\n,]/)
    .map((v) => v.trim())
    .filter(Boolean);

  // Only providers that are both in scope and actually usable right now.
  const available = new Set(
    (sources ?? []).filter((s) => s.enabled && s.health !== 'unavailable').map((s) => s.name),
  );
  const selectedProviders = SCOPE.filter((s) => enabled.has(s.id))
    .flatMap((s) => s.providers)
    .filter((p) => available.has(p));
  const skipped = SCOPE.filter((s) => enabled.has(s.id))
    .flatMap((s) => s.providers)
    .filter((p) => !available.has(p));

  const create = useMutation({
    mutationFn: async () => {
      const investigation = await api.createInvestigation({
        name: name.trim() || targetList[0] || 'Investigation',
        targets: targetList.map((value) => ({ value })),
      });
      await api.startScan(investigation.id, {
        providers: selectedProviders.length ? selectedProviders : undefined,
        max_depth: depth,
        recursive: true,
      });
      return investigation;
    },
    onSuccess: (investigation) => {
      void queryClient.invalidateQueries({ queryKey: ['investigations'] });
      navigate(`/investigations/${investigation.id}`);
    },
    onError: (err: Error) => setError(err.message),
  });

  function toggle(id: string) {
    setEnabled((current) => {
      const next = new Set(current);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  return (
    <section className="rounded border border-line bg-ink-850">
      <header className="flex items-center gap-3 border-b border-line px-4 py-2.5">
        <h2 className="text-xs font-semibold text-fg">New investigation</h2>
        <ol className="ml-auto flex items-center gap-1.5" aria-label="Progress">
          {['Target', 'What to search', 'Start'].map((label, index) => (
            <li key={label} className="flex items-center gap-1.5">
              <span
                className={clsx(
                  'rounded px-1.5 py-0.5 text-2xs',
                  step === index + 1
                    ? 'bg-accent/20 text-accent'
                    : step > index + 1
                      ? 'text-observed'
                      : 'text-fg-dim',
                )}
              >
                {index + 1}. {label}
              </span>
              {index < 2 && <span className="text-fg-dim">›</span>}
            </li>
          ))}
        </ol>
      </header>

      <div className="p-4">
        {step === 1 && (
          <div>
            <label htmlFor="wizard-targets" className="mb-1 block text-xs text-fg">
              What do you want to investigate?
            </label>
            <p className="mb-2 text-2xs text-fg-dim">
              A username, a profile link, an email address, a domain or a URL. One per line.
            </p>
            <textarea
              id="wizard-targets"
              value={targets}
              onChange={(event) => setTargets(event.target.value)}
              rows={3}
              placeholder={EXAMPLES.join('\n')}
              className="w-full rounded border border-line bg-ink-900 px-2 py-1.5 font-mono text-xs text-fg outline-none focus:border-accent"
            />
            <div className="mt-2 flex flex-wrap gap-1.5">
              <span className="text-2xs text-fg-dim">Try:</span>
              {EXAMPLES.map((example) => (
                <button
                  key={example}
                  onClick={() => setTargets((t) => (t ? `${t}\n${example}` : example))}
                  className="rounded border border-line px-1.5 py-0.5 text-2xs text-fg-muted hover:border-accent hover:text-accent"
                >
                  {example}
                </button>
              ))}
            </div>
          </div>
        )}

        {step === 2 && (
          <div className="space-y-2">
            <p className="text-xs text-fg">What should we search?</p>
            {SCOPE.map((option) => (
              <label
                key={option.id}
                className="flex cursor-pointer items-start gap-2 rounded border border-line bg-ink-900 px-2.5 py-2 hover:border-line-bright"
              >
                <input
                  type="checkbox"
                  checked={enabled.has(option.id)}
                  onChange={() => toggle(option.id)}
                  className="mt-0.5 accent-accent"
                />
                <span className="min-w-0">
                  <span className="block text-xs text-fg">{option.label}</span>
                  <span className="block text-2xs text-fg-dim">{option.hint}</span>
                </span>
              </label>
            ))}

            <button
              onClick={() => setShowAdvanced((v) => !v)}
              className="text-2xs text-accent hover:underline"
            >
              {showAdvanced ? 'Hide' : 'Show'} advanced options
            </button>
            {showAdvanced && (
              <div className="rounded border border-line bg-ink-900 p-2.5">
                <label className="mb-1 block text-2xs text-fg-dim">
                  How far to follow leads: depth {depth}
                </label>
                <input
                  type="range"
                  min={1}
                  max={3}
                  value={depth}
                  onChange={(event) => setDepth(Number(event.target.value))}
                  className="w-full accent-accent"
                />
                <p className="text-2xs text-fg-dim">
                  Each step follows identifiers found in the previous one. Deeper searches
                  take longer and return weaker leads.
                </p>
                {selectedProviders.length > 0 && (
                  <p className="mt-2 text-2xs text-fg-muted">
                    Sources: {selectedProviders.join(', ')}
                  </p>
                )}
              </div>
            )}
          </div>
        )}

        {step === 3 && (
          <div className="space-y-2 text-xs">
            <div>
              <span className="text-fg-dim">Targets</span>
              <ul className="mt-0.5">
                {targetList.map((target) => (
                  <li key={target} className="font-mono text-fg">
                    {target}
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <span className="text-fg-dim">Searching</span>
              <p className="text-fg">
                {SCOPE.filter((s) => enabled.has(s.id))
                  .map((s) => s.label.toLowerCase())
                  .join(', ') || 'nothing selected'}
              </p>
            </div>
            <Input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder={`Name (optional) — defaults to "${targetList[0] ?? 'Investigation'}"`}
            />
            {skipped.length > 0 && (
              <p className="rounded border border-inferred/40 bg-inferred/5 px-2 py-1.5 text-2xs text-inferred">
                Not available right now and will be skipped: {skipped.join(', ')}. Check the
                Sources tab to see why.
              </p>
            )}
            <p className="text-2xs text-fg-dim">
              Only public information is collected. Results are correlations, not verified
              identities — check the evidence before relying on any of them.
            </p>
          </div>
        )}

        {error && <p className="mt-2 text-2xs text-danger">{error}</p>}

        <div className="mt-4 flex items-center gap-2">
          {step > 1 && (
            <Button variant="ghost" onClick={() => setStep((s) => s - 1)}>
              Back
            </Button>
          )}
          {step < 3 && (
            <Button
              variant="primary"
              disabled={step === 1 && targetList.length === 0}
              onClick={() => setStep((s) => s + 1)}
            >
              Continue
            </Button>
          )}
          {step === 3 && (
            <Button
              variant="primary"
              disabled={create.isPending || targetList.length === 0}
              onClick={() => {
                setError(null);
                create.mutate();
              }}
            >
              {create.isPending ? 'Starting…' : 'Start investigation'}
            </Button>
          )}
          {onCancel && (
            <Button variant="ghost" onClick={onCancel} className="ml-auto">
              Cancel
            </Button>
          )}
        </div>
      </div>
    </section>
  );
}
