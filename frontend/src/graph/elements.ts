import type { ElementDefinition, LayoutOptions } from 'cytoscape';
import type { GraphPayload } from '@/api/types';
import { ASSERTION_COLORS, colorForType } from './style';

const MIN_SIZE = 18;
const MAX_SIZE = 52;

/** Convert the API payload into Cytoscape elements. Pure, so it is unit-testable. */
export function toElements(graph: GraphPayload): ElementDefinition[] {
  const maxDegree = Math.max(1, ...graph.nodes.map((n) => n.degree));

  const nodes: ElementDefinition[] = graph.nodes.map((node) => ({
    group: 'nodes',
    data: {
      id: node.id,
      label: node.label.length > 42 ? `${node.label.slice(0, 39)}…` : node.label,
      fullLabel: node.label,
      type: node.type,
      color: colorForType(node.type),
      confidence: node.confidence,
      displayConfidence: node.display_confidence,
      isStale: node.is_stale,
      isTarget: node.is_target,
      isCluster: node.is_cluster,
      cluster: node.cluster,
      degree: node.degree,
      size: Math.round(MIN_SIZE + (MAX_SIZE - MIN_SIZE) * Math.sqrt(node.degree / maxDegree)),
    },
  }));

  const edges: ElementDefinition[] = graph.edges.map((edge) => ({
    group: 'edges',
    data: {
      id: edge.id,
      source: edge.source,
      target: edge.target,
      label: edge.type.replace(/_/g, ' ').toLowerCase(),
      type: edge.type,
      color: ASSERTION_COLORS[edge.assertion] ?? '#8b93a7',
      // Confidence is legible as line weight before anything is clicked.
      width: 1 + edge.confidence * 2.5,
      confidence: edge.confidence,
      assertion: edge.assertion,
      isCorrelation: edge.is_correlation,
    },
  }));

  return [...nodes, ...edges];
}

export type LayoutName = 'cose' | 'concentric' | 'breadthfirst' | 'grid' | 'circle';

export function layoutOptions(name: LayoutName, nodeCount: number): LayoutOptions {
  const animate = nodeCount <= 400;
  switch (name) {
    case 'concentric':
      return {
        name: 'concentric',
        animate,
        concentric: (node) => Number(node.data('degree')),
        levelWidth: () => 2,
        minNodeSpacing: 30,
      };
    case 'breadthfirst':
      return { name: 'breadthfirst', animate, spacingFactor: 1.3, directed: true };
    case 'grid':
      return { name: 'grid', animate, avoidOverlap: true };
    case 'circle':
      return { name: 'circle', animate, avoidOverlap: true };
    default:
      return {
        name: 'cose',
        animate,
        nodeRepulsion: 9000,
        idealEdgeLength: 110,
        nestingFactor: 1.1,
        gravity: 0.3,
        numIter: nodeCount > 800 ? 350 : 1000,
        randomize: false,
      };
  }
}
