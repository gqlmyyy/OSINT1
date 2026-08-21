import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { api } from '@/api/client';
import type { Investigation, PathResult } from '@/api/types';
import { Button, Input } from '@/components/ui';
import type { LayoutName } from '@/graph/elements';
import { useInvestigationStore } from '@/store/investigation';
import { StageTrail } from './ScanProgress';

const LAYOUTS: LayoutName[] = ['cose', 'concentric', 'breadthfirst', 'circle', 'grid'];
const EXPORT_FORMATS = ['json', 'csv', 'markdown', 'html', 'graphml'] as const;

interface Props {
  investigation: Investigation;
  layout: LayoutName;
  onLayout: (layout: LayoutName) => void;
  onPath: (result: PathResult | null) => void;
}

export function Toolbar({ investigation, layout, onLayout, onPath }: Props) {
  const queryClient = useQueryClient();
  const [scanning, setScanning] = useState(false);
  const [pathMode, setPathMode] = useState<string | null>(null);
  const filters = useInvestigationStore((s) => s.filters);
  const setFilter = useInvestigationStore((s) => s.setFilter);
  const stage = useInvestigationStore((s) => s.stage);
  const selectedNodeId = useInvestigationStore((s) => s.selectedNodeId);
  const clearActivity = useInvestigationStore((s) => s.clearActivity);

  async function runScan() {
    setScanning(true);
    clearActivity();
    try {
      await api.startScan(investigation.id, { recursive: true });
      await queryClient.invalidateQueries({ queryKey: ['investigation', investigation.id] });
    } finally {
      setScanning(false);
    }
  }

  async function findPath() {
    if (!selectedNodeId) return;
    if (!pathMode) {
      setPathMode(selectedNodeId);
      onPath(null);
      return;
    }
    const result = await api.shortestPath(investigation.id, pathMode, selectedNodeId);
    onPath(result);
    setPathMode(null);
  }

  async function exportAs(format: string) {
    const blob = await api.exportInvestigation(investigation.id, { format, scope: 'investigation' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `${investigation.name.replace(/\W+/g, '-').toLowerCase()}.${format === 'markdown' ? 'md' : format}`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-line bg-ink-850 px-3 py-2">
      <Button variant="primary" onClick={runScan} disabled={scanning || investigation.targets.length === 0}>
        {scanning ? 'Starting…' : 'Run scan'}
      </Button>

      <div className="h-4 w-px bg-line" />

      <Input
        value={filters.search}
        onChange={(event) => setFilter('search', event.target.value)}
        placeholder="Find in graph…"
        aria-label="Find in graph"
        className="w-48"
      />

      <select
        value={layout}
        onChange={(event) => onLayout(event.target.value as LayoutName)}
        aria-label="Graph layout"
        className="rounded border border-line bg-ink-900 px-2 py-1 text-xs text-fg-muted outline-none focus:border-accent"
      >
        {LAYOUTS.map((name) => (
          <option key={name} value={name}>
            {name} layout
          </option>
        ))}
      </select>

      <Button
        onClick={findPath}
        disabled={!selectedNodeId}
        title="Select a node, click once to set the start, select another node and click again"
      >
        {pathMode ? 'Pick end node' : 'Shortest path'}
      </Button>

      <div className="ml-auto flex items-center gap-2">
        <StageTrail stage={stage === 'idle' ? investigation.stage : stage} />
        <div className="h-4 w-px bg-line" />
        <select
          onChange={(event) => {
            if (event.target.value) void exportAs(event.target.value);
            event.target.value = '';
          }}
          aria-label="Export investigation"
          className="rounded border border-line bg-ink-900 px-2 py-1 text-xs text-fg-muted outline-none focus:border-accent"
        >
          <option value="">Export…</option>
          {EXPORT_FORMATS.map((format) => (
            <option key={format} value={format}>
              {format.toUpperCase()}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}
