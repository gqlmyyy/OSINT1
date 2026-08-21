import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import cytoscape, { type Core, type NodeSingular } from 'cytoscape';
import type { GraphPayload } from '@/api/types';
import { useInvestigationStore, visibleGraph } from '@/store/investigation';
import { toElements, type LayoutName, layoutOptions } from './elements';
import { cytoscapeStyle } from './style';

interface Props {
  graph: GraphPayload;
  layout: LayoutName;
  highlightPath: string[];
  onExpandCluster: (cluster: string) => void;
}

export function GraphCanvas({ graph, layout, highlightPath, onExpandCluster }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const cy = useRef<Core | null>(null);
  const [ready, setReady] = useState(false);

  const filters = useInvestigationStore((s) => s.filters);
  const selectNode = useInvestigationStore((s) => s.selectNode);
  const selectEdge = useInvestigationStore((s) => s.selectEdge);
  const toggleCollapsed = useInvestigationStore((s) => s.toggleCollapsed);

  const filtered = useMemo(() => visibleGraph(graph, filters), [graph, filters]);
  const elements = useMemo(() => toElements(filtered), [filtered]);

  // -- one-time instance ---------------------------------------------------
  useEffect(() => {
    if (!container.current || cy.current) return;
    const instance = cytoscape({
      container: container.current,
      style: cytoscapeStyle,
      wheelSensitivity: 0.25,
      minZoom: 0.05,
      maxZoom: 4,
      // Rendering is capped rather than animated during interaction, so a large graph
      // still pans and zooms smoothly.
      textureOnViewport: true,
      pixelRatio: 1,
      motionBlur: false,
    });
    cy.current = instance;
    setReady(true);
    return () => {
      instance.destroy();
      cy.current = null;
    };
  }, []);

  // -- events --------------------------------------------------------------
  useEffect(() => {
    const instance = cy.current;
    if (!instance) return;

    const onNodeTap = (event: cytoscape.EventObject) => {
      const node = event.target as NodeSingular;
      if (node.data('isCluster')) {
        onExpandCluster(String(node.data('cluster')));
        return;
      }
      selectNode(node.id());
    };
    const onEdgeTap = (event: cytoscape.EventObject) => selectEdge(event.target.id());
    const onBackground = (event: cytoscape.EventObject) => {
      if (event.target === instance) selectNode(null);
    };
    const onNodeDouble = (event: cytoscape.EventObject) => toggleCollapsed(event.target.id());

    instance.on('tap', 'node', onNodeTap);
    instance.on('tap', 'edge', onEdgeTap);
    instance.on('tap', onBackground);
    instance.on('dbltap', 'node', onNodeDouble);
    return () => {
      instance.off('tap', 'node', onNodeTap);
      instance.off('tap', 'edge', onEdgeTap);
      instance.off('tap', onBackground);
      instance.off('dbltap', 'node', onNodeDouble);
    };
  }, [selectNode, selectEdge, toggleCollapsed, onExpandCluster]);

  // -- data + layout -------------------------------------------------------
  useEffect(() => {
    const instance = cy.current;
    if (!instance || !ready) return;

    const previous = new Map(instance.nodes().map((n) => [n.id(), n.position()]));
    instance.elements().remove();
    instance.add(elements);
    // Preserve positions of nodes we already had, so live additions do not reshuffle
    // the analyst's mental map of the graph.
    let reused = 0;
    instance.nodes().forEach((node) => {
      const position = previous.get(node.id());
      if (position) {
        node.position(position);
        reused += 1;
      }
    });
    const newNodes = instance.nodes().length - reused;
    if (newNodes > 0 || reused === 0) {
      instance.layout(layoutOptions(layout, instance.nodes().length)).run();
    }
  }, [elements, layout, ready]);

  // -- path highlight ------------------------------------------------------
  useEffect(() => {
    const instance = cy.current;
    if (!instance) return;
    instance.elements().removeClass('dimmed highlighted');
    if (highlightPath.length < 2) return;

    const ids = new Set(highlightPath);
    instance.elements().addClass('dimmed');
    instance.nodes().filter((n) => ids.has(n.id())).removeClass('dimmed').addClass('highlighted');
    instance
      .edges()
      .filter((e) => ids.has(e.source().id()) && ids.has(e.target().id()))
      .removeClass('dimmed')
      .addClass('highlighted');
  }, [highlightPath, elements]);

  const fit = useCallback(() => cy.current?.animate({ fit: { eles: cy.current.elements(), padding: 40 }, duration: 250 }), []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) return;
      if (event.key === 'f') fit();
      if (event.key === '+' || event.key === '=') cy.current?.zoom(cy.current.zoom() * 1.2);
      if (event.key === '-') cy.current?.zoom(cy.current.zoom() / 1.2);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [fit]);

  return (
    <div className="relative h-full w-full">
      <div ref={container} className="h-full w-full" data-testid="graph-canvas" />
      <div className="pointer-events-none absolute bottom-3 left-3 flex gap-3 text-2xs text-fg-dim">
        <span>{filtered.nodes.length} nodes</span>
        <span>{filtered.edges.length} edges</span>
        <span className="text-fg-dim/70">click to inspect · double-click to collapse · f to fit</span>
      </div>
      <div className="pointer-events-auto absolute right-3 top-3 flex flex-col gap-1">
        <CanvasButton label="Fit" onClick={fit} />
        <CanvasButton label="+" onClick={() => cy.current?.zoom(cy.current.zoom() * 1.2)} />
        <CanvasButton label="−" onClick={() => cy.current?.zoom(cy.current.zoom() / 1.2)} />
      </div>
    </div>
  );
}

function CanvasButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="h-7 w-9 rounded border border-line bg-ink-850/90 text-xs text-fg-muted transition hover:border-line-bright hover:text-fg"
    >
      {label}
    </button>
  );
}
