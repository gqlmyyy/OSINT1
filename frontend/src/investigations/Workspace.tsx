import { useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api/client';
import { InvestigationSocket } from '@/api/ws';
import type { PathResult } from '@/api/types';
import { Button, Empty, Panel } from '@/components/ui';
import { EntityInspector } from '@/entities/EntityInspector';
import { EvidencePanel } from '@/evidence/EvidencePanel';
import { GraphCanvas } from '@/graph/GraphCanvas';
import type { LayoutName } from '@/graph/elements';
import { useInvestigationStore } from '@/store/investigation';
import { Timeline } from '@/timeline/Timeline';
import { Findings, Interactions } from './Findings';
import { Sidebar } from './Sidebar';
import { Toolbar } from './Toolbar';

type BottomTab = 'findings' | 'interactions' | 'evidence' | 'timeline' | 'path';

export function Workspace() {
  const { id = '' } = useParams();
  const queryClient = useQueryClient();
  const [layout, setLayout] = useState<LayoutName>('cose');
  const [path, setPath] = useState<PathResult | null>(null);
  const [bottomTab, setBottomTab] = useState<BottomTab>('findings');
  const [expand, setExpand] = useState<string | undefined>();
  const [connection, setConnection] = useState<'open' | 'closed' | 'error'>('closed');

  const setGraph = useInvestigationStore((s) => s.setGraph);
  const applyEvent = useInvestigationStore((s) => s.applyEvent);
  const storedGraph = useInvestigationStore((s) => s.graph);

  const investigationQuery = useQuery({
    queryKey: ['investigation', id],
    queryFn: () => api.getInvestigation(id),
    enabled: Boolean(id),
  });

  const graphQuery = useQuery({
    queryKey: ['graph', id, expand],
    queryFn: () => api.graph(id, { expand }),
    enabled: Boolean(id),
  });

  useEffect(() => {
    if (graphQuery.data) setGraph(graphQuery.data);
  }, [graphQuery.data, setGraph]);

  // Live updates: nodes and edges appear as they are found, without a full refetch.
  useEffect(() => {
    if (!id) return;
    const socket = new InvestigationSocket(
      id,
      (event) => {
        applyEvent(event);
        if (event.event === 'investigation_completed') {
          void queryClient.invalidateQueries({ queryKey: ['investigation', id] });
          void queryClient.invalidateQueries({ queryKey: ['matches', id] });
          void queryClient.invalidateQueries({ queryKey: ['findings', id] });
          void queryClient.invalidateQueries({ queryKey: ['interactions', id] });
          void queryClient.invalidateQueries({ queryKey: ['observations', id] });
          void queryClient.invalidateQueries({ queryKey: ['timeline', id] });
        }
      },
      setConnection,
    );
    socket.connect();
    return () => socket.close();
  }, [id, applyEvent, queryClient]);

  const pathIds = useMemo(() => path?.steps.map((step) => step.entity_id) ?? [], [path]);

  if (investigationQuery.isLoading) {
    return <div className="grid h-full place-items-center text-xs text-fg-dim">Loading investigation…</div>;
  }
  if (!investigationQuery.data) {
    return <div className="grid h-full place-items-center text-xs text-fg-dim">Investigation not found.</div>;
  }

  const investigation = investigationQuery.data;

  return (
    <div className="grid h-full min-h-0 grid-cols-[16rem_1fr_20rem] grid-rows-[auto_1fr_16rem]">
      <div className="col-span-3">
        <Toolbar
          investigation={investigation}
          layout={layout}
          onLayout={setLayout}
          onPath={(result) => {
            setPath(result);
            if (result) setBottomTab('path');
          }}
        />
      </div>

      <aside className="row-span-2 min-h-0 overflow-hidden">
        <Sidebar investigation={investigation} />
      </aside>

      <main className="relative min-h-0 bg-ink-900">
        {storedGraph?.clustered && (
          <div className="absolute left-3 top-3 z-10 rounded border border-accent/40 bg-ink-850/95 px-2 py-1 text-2xs text-accent">
            Large graph — showing clusters. Click a cluster to expand it.
            {expand && (
              <button className="ml-2 underline" onClick={() => setExpand(undefined)}>
                back to clusters
              </button>
            )}
          </div>
        )}
        <div className="absolute right-3 bottom-3 z-10 flex items-center gap-1.5 text-2xs text-fg-dim">
          <span
            className={
              connection === 'open'
                ? 'h-1.5 w-1.5 rounded-full bg-observed'
                : 'h-1.5 w-1.5 rounded-full bg-unverified'
            }
          />
          {connection === 'open' ? 'live' : 'reconnecting'}
        </div>
        {storedGraph ? (
          storedGraph.nodes.length === 0 ? (
            <Empty>No entities yet. Add targets and run a scan, or seed the demo investigation.</Empty>
          ) : (
            <GraphCanvas
              graph={storedGraph}
              layout={layout}
              highlightPath={pathIds}
              onExpandCluster={setExpand}
            />
          )
        ) : (
          <div className="grid h-full place-items-center text-xs text-fg-dim">Loading graph…</div>
        )}
      </main>

      <aside className="row-span-2 min-h-0 overflow-hidden border-l border-line">
        <EntityInspector onExpanded={() => graphQuery.refetch()} />
      </aside>

      <section className="col-start-2 min-h-0 border-t border-line">
        <div className="flex h-full min-h-0 flex-col">
          <nav className="flex shrink-0 gap-1 border-b border-line bg-ink-850 px-2" role="tablist">
            {(['findings', 'interactions', 'evidence', 'timeline', 'path'] as BottomTab[]).map((tab) => (
              <button
                key={tab}
                role="tab"
                aria-selected={bottomTab === tab}
                onClick={() => setBottomTab(tab)}
                className={
                  bottomTab === tab
                    ? 'border-b-2 border-accent px-2 py-1.5 text-2xs uppercase tracking-wider text-fg'
                    : 'border-b-2 border-transparent px-2 py-1.5 text-2xs uppercase tracking-wider text-fg-dim hover:text-fg-muted'
                }
              >
                {tab}
              </button>
            ))}
          </nav>
          <div className="min-h-0 flex-1">
            {bottomTab === 'findings' && <Findings investigationId={id} />}
            {bottomTab === 'interactions' && <Interactions investigationId={id} />}
            {bottomTab === 'evidence' && <EvidencePanel investigationId={id} />}
            {bottomTab === 'timeline' && <Timeline investigationId={id} />}
            {bottomTab === 'path' && <PathPanel path={path} />}
          </div>
        </div>
      </section>
    </div>
  );
}

function PathPanel({ path }: { path: PathResult | null }) {
  if (!path) {
    return (
      <Panel>
        <Empty>
          Select a node, click <em>Shortest path</em>, then select a second node and click again.
        </Empty>
      </Panel>
    );
  }
  if (!path.found) {
    return (
      <Panel>
        <Empty>No path connects these two entities in the current graph.</Empty>
      </Panel>
    );
  }
  return (
    <Panel
      title="Shortest path"
      actions={
        <Button variant="ghost" onClick={() => navigator.clipboard.writeText(path.markdown)}>
          Copy as report
        </Button>
      }
    >
      <ol className="space-y-0 px-3 py-2">
        {path.steps.map((step, index) => (
          <li key={step.entity_id}>
            {index > 0 && (
              <p className="py-0.5 pl-3 text-2xs text-accent">
                ↓ {step.via}
                {step.via_confidence !== null && ` (${Math.round(step.via_confidence * 100)}%)`}
                {step.why && <span className="ml-2 text-fg-dim">{step.why}</span>}
              </p>
            )}
            <p className="text-xs text-fg">
              <span className="text-fg-dim">{step.type}</span> · {step.label}
            </p>
          </li>
        ))}
      </ol>
    </Panel>
  );
}
