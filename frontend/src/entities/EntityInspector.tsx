import { useQuery } from '@tanstack/react-query';
import { api } from '@/api/client';
import {
  AssertionBadge,
  Button,
  Confidence,
  Empty,
  ExternalLink,
  KeyValue,
  Panel,
  formatDate,
} from '@/components/ui';
import { useInvestigationStore } from '@/store/investigation';
import { colorForType } from '@/graph/style';

/**
 * The "what is this, where did it come from, when was it seen, why is it linked, how
 * confident are we, what is the evidence" panel (spec 43).
 */
export function EntityInspector({ onExpanded }: { onExpanded?: () => void }) {
  const entityId = useInvestigationStore((s) => s.selectedNodeId);
  const edgeId = useInvestigationStore((s) => s.selectedEdgeId);

  if (edgeId) return <RelationshipInspector relationshipId={edgeId} />;
  if (!entityId) {
    return (
      <Panel title="Entity inspector">
        <Empty>Select a node to see what it is, where it came from, and why it is linked.</Empty>
      </Panel>
    );
  }
  return <EntityDetailPanel entityId={entityId} onExpanded={onExpanded} />;
}

function EntityDetailPanel({ entityId, onExpanded }: { entityId: string; onExpanded?: () => void }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['entity', entityId],
    queryFn: () => api.entity(entityId),
  });

  if (isLoading) return <Panel title="Entity inspector"><Empty>Loading…</Empty></Panel>;
  if (error || !data) {
    return <Panel title="Entity inspector"><Empty>Could not load this entity.</Empty></Panel>;
  }

  const attributes = Object.entries(data.attributes).filter(
    ([key, value]) => value !== null && value !== '' && key !== 'is_target' && typeof value !== 'object',
  );

  return (
    <Panel
      title="Entity inspector"
      actions={
        <Button
          variant="ghost"
          onClick={async () => {
            await api.expandEntity(entityId);
            onExpanded?.();
          }}
          title="Queue a bounded recursive search seeded from this node"
        >
          Expand
        </Button>
      }
    >
      <div className="border-b border-line px-3 py-2">
        <div className="flex items-start gap-2">
          <span
            className="mt-1 h-2.5 w-2.5 shrink-0 rounded-full"
            style={{ backgroundColor: colorForType(data.type) }}
          />
          <div className="min-w-0">
            <p className="break-words text-sm font-medium text-fg">{data.label}</p>
            <p className="text-2xs uppercase tracking-wider text-fg-dim">{data.type}</p>
          </div>
        </div>
        <div className="mt-2 flex items-center gap-2">
          <span className="text-2xs text-fg-dim">Confidence</span>
          <Confidence value={data.confidence} />
        </div>
      </div>

      <dl className="border-b border-line py-1">
        <KeyValue label="Canonical key">
          <code className="text-2xs text-fg-muted">{data.canonical_key}</code>
        </KeyValue>
        <KeyValue label="Sources">{data.sources.join(', ') || '—'}</KeyValue>
        <KeyValue label="First seen">{formatDate(data.first_seen)}</KeyValue>
        <KeyValue label="Last seen">{formatDate(data.last_seen)}</KeyValue>
        <KeyValue label="Observations">{data.observation_count}</KeyValue>
        <KeyValue label="Relationships">{data.relationships.length}</KeyValue>
        {attributes.map(([key, value]) => (
          <KeyValue key={key} label={key.replace(/_/g, ' ')}>
            {String(value)}
          </KeyValue>
        ))}
      </dl>

      {data.identifiers.length > 0 && (
        <Section title="Identifiers">
          <ul className="space-y-1 px-3 py-2 text-xs">
            {data.identifiers.map((identifier) => (
              <li key={`${identifier.kind}:${identifier.normalized}`} className="flex gap-2">
                <span className="shrink-0 text-fg-dim">{identifier.kind}</span>
                <span className="break-all text-fg">{identifier.value}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {data.external_links.length > 0 && (
        <Section title="External links">
          <ul className="space-y-1 px-3 py-2 text-xs">
            {data.external_links.map((link) => (
              <li key={link}>
                <ExternalLink href={link} />
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section title="Evidence">
        {data.recent_observations.length === 0 ? (
          <Empty>No observations recorded.</Empty>
        ) : (
          <ul className="divide-y divide-line/60">
            {data.recent_observations.map((observation) => (
              <li key={observation.id} className="px-3 py-2 text-xs">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium text-fg">{observation.provider}</span>
                  <AssertionBadge assertion={observation.assertion} short />
                </div>
                <p className="mt-0.5 text-2xs text-fg-dim">{formatDate(observation.observed_at)}</p>
                {observation.url && (
                  <p className="mt-1">
                    <ExternalLink href={observation.url}>{observation.url}</ExternalLink>
                  </p>
                )}
                {observation.evidence[0]?.excerpt && (
                  <p className="mt-1 border-l-2 border-line pl-2 text-fg-muted">
                    {observation.evidence[0].excerpt}
                  </p>
                )}
                {observation.evidence[0]?.sha256 && (
                  <p className="mt-1 text-2xs text-fg-dim">
                    sha256 {observation.evidence[0].sha256.slice(0, 16)}…
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Raw data">
        <pre className="max-h-64 overflow-auto px-3 py-2 text-2xs leading-relaxed text-fg-muted">
          {JSON.stringify(data.attributes, null, 2)}
        </pre>
      </Section>
    </Panel>
  );
}

function RelationshipInspector({ relationshipId }: { relationshipId: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ['relationship', relationshipId],
    queryFn: () => api.relationship(relationshipId),
  });

  if (isLoading) return <Panel title="Relationship"><Empty>Loading…</Empty></Panel>;
  if (!data) return <Panel title="Relationship"><Empty>Could not load this relationship.</Empty></Panel>;

  return (
    <Panel title="Why does this relationship exist?">
      <div className="border-b border-line px-3 py-2 text-xs">
        <p className="text-fg">{data.source_label}</p>
        <p className="my-1 text-2xs text-accent">↓ {data.type}</p>
        <p className="text-fg">{data.target_label}</p>
      </div>
      <dl className="border-b border-line py-1">
        <KeyValue label="Classification">
          <AssertionBadge assertion={data.assertion} />
        </KeyValue>
        <KeyValue label="Confidence">
          <Confidence value={data.confidence} />
        </KeyValue>
        <KeyValue label="Source">{data.provider}</KeyValue>
        <KeyValue label="Recorded">{formatDate(data.created_at)}</KeyValue>
      </dl>
      <Section title="Reasoning">
        <p className="px-3 py-2 text-xs leading-relaxed text-fg">{data.why || 'No stated reason.'}</p>
      </Section>
      {data.evidence_url && (
        <Section title="Evidence URL">
          <p className="px-3 py-2 text-xs">
            <ExternalLink href={data.evidence_url} />
          </p>
        </Section>
      )}
      {Array.isArray(data.evidence.reasons) && (
        <Section title="Correlation signals">
          <ul className="space-y-1 px-3 py-2 text-xs">
            {(data.evidence.reasons as string[]).map((reason) => (
              <li
                key={reason}
                className={reason.startsWith('+') ? 'text-observed' : 'text-inferred'}
              >
                {reason}
              </li>
            ))}
          </ul>
        </Section>
      )}
      {data.observation && (
        <Section title="Underlying observation">
          <pre className="max-h-56 overflow-auto px-3 py-2 text-2xs text-fg-muted">
            {JSON.stringify(data.observation.data, null, 2)}
          </pre>
        </Section>
      )}
    </Panel>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="border-b border-line last:border-b-0">
      <h3 className="px-3 pt-2 text-2xs font-semibold uppercase tracking-wider text-fg-dim">
        {title}
      </h3>
      {children}
    </div>
  );
}
