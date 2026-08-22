import type { StylesheetStyle } from 'cytoscape';

/** One colour per entity type, kept in sync with the legend and the entity inspector. */
export const NODE_COLORS: Record<string, string> = {
  person: '#c58cff',
  username: '#5aa9ff',
  email: '#ffa94d',
  phone: '#ffd43b',
  social_account: '#3ddc97',
  domain: '#4dabf7',
  url: '#748ffc',
  ip: '#ff8787',
  repository: '#63e6be',
  organization: '#e599f7',
  company: '#e599f7',
  location: '#ffc9c9',
  avatar: '#ffe066',
  image: '#ffe066',
  website: '#38d9a9',
  crypto_hash: '#adb5bd',
  technology: '#9775fa',
  cluster: '#495057',
};

export const ASSERTION_COLORS: Record<string, string> = {
  observed: '#3ddc97',
  correlated: '#5aa9ff',
  inferred: '#f5b942',
  unverified: '#8b93a7',
};

export function colorForType(type: string): string {
  return NODE_COLORS[type] ?? '#868e96';
}

export const cytoscapeStyle: StylesheetStyle[] = [
  {
    selector: 'node',
    style: {
      'background-color': 'data(color)',
      'border-width': 1.5,
      'border-color': '#0b0e14',
      label: 'data(label)',
      color: '#e6e9ef',
      'font-size': 10,
      'font-family': 'Inter, system-ui, sans-serif',
      'text-valign': 'bottom',
      'text-margin-y': 5,
      'text-wrap': 'ellipsis',
      'text-max-width': '120px',
      // Degree drives size, so hubs are visible before you read a single label.
      width: 'data(size)',
      height: 'data(size)',
      'transition-property': 'background-color, border-color, width, height',
      'transition-duration': 150,
    },
  },
  {
    selector: 'node[?isTarget]',
    style: {
      'border-width': 3,
      'border-color': '#ffffff',
      'font-weight': 'bold',
      'font-size': 12,
    },
  },
  {
    // Confidence decay (evidence has aged past the staleness threshold): reduced visual
    // weight rather than an alarming color, matching the "coming-soon/limited" treatment
    // used everywhere else — a dashed border, not a warning color.
    selector: 'node[?isStale]',
    style: { 'border-style': 'dashed', 'border-color': '#8b93a7' },
  },
  {
    selector: 'node[?isCluster]',
    style: {
      shape: 'round-rectangle',
      'background-opacity': 0.35,
      'border-width': 2,
      'border-style': 'dashed',
      'border-color': '#5aa9ff',
      'font-size': 12,
    },
  },
  {
    selector: 'node.dimmed',
    style: { opacity: 0.18, 'text-opacity': 0.15 },
  },
  {
    selector: 'node:selected',
    style: { 'border-width': 3, 'border-color': '#ffd43b', 'overlay-opacity': 0 },
  },
  {
    selector: 'node.highlighted',
    style: { 'border-width': 3, 'border-color': '#ffd43b' },
  },
  {
    selector: 'edge',
    style: {
      width: 'data(width)',
      'line-color': 'data(color)',
      'target-arrow-color': 'data(color)',
      'target-arrow-shape': 'triangle',
      'arrow-scale': 0.8,
      'curve-style': 'bezier',
      opacity: 0.65,
      label: 'data(label)',
      'font-size': 8,
      color: '#8b93a7',
      'text-rotation': 'autorotate',
      'text-background-color': '#0b0e14',
      'text-background-opacity': 0.85,
      'text-background-padding': '2px',
    },
  },
  {
    selector: 'edge[?isCorrelation]',
    style: { 'line-style': 'dashed', opacity: 0.8 },
  },
  { selector: 'edge.dimmed', style: { opacity: 0.06, 'text-opacity': 0 } },
  {
    selector: 'edge:selected, edge.highlighted',
    style: { width: 4, opacity: 1, 'line-color': '#ffd43b', 'target-arrow-color': '#ffd43b' },
  },
];
