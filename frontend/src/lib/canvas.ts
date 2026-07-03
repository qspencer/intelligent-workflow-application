import dagre from 'dagre';

import type {
  AgenticStep,
  DeterministicStep,
  StepState,
  WorkflowDefinition,
  WorkflowEdge,
  WorkflowStep,
} from '../types';

export const TRIGGER_NODE_ID = '__trigger__';

const NODE_WIDTH = 230;
const NODE_HEIGHT = 76;

/** Data carried on each canvas node, consumed by the custom node components. */
export interface CanvasNodeData {
  kind: 'trigger' | 'deterministic' | 'agentic';
  title: string;
  subtitle: string;
  icon: string;
  step?: WorkflowStep;
  trigger?: WorkflowDefinition['trigger'];
  /** Live step state when the canvas is following an instance (C2). */
  status?: StepState;
  [key: string]: unknown;
}

/** Plain-language presentation for a step's live state — drives node coloring. */
export interface StatusMeta {
  label: string;
  cssClass: string;
  icon: string;
}

export function statusMeta(state: StepState): StatusMeta {
  switch (state) {
    case 'running':
      return { label: 'Running…', cssClass: 'running', icon: '⟳' };
    case 'completed':
      return { label: 'Done', cssClass: 'completed', icon: '✓' };
    case 'failed':
      return { label: 'Failed', cssClass: 'failed', icon: '✗' };
    case 'skipped':
      return { label: 'Skipped', cssClass: 'skipped', icon: '–' };
    case 'pending':
    default:
      return { label: 'Waiting', cssClass: 'pending', icon: '…' };
  }
}

export interface CanvasNode {
  id: string;
  type: 'trigger' | 'deterministic' | 'agentic';
  position: { x: number; y: number };
  data: CanvasNodeData;
}

export interface CanvasEdge {
  id: string;
  source: string;
  target: string;
  label?: string;
}

// ---- Friendly labels ----

/** snake_case / kebab-case → "Title case words". */
export function humanize(raw: string): string {
  const words = raw.replace(/[_-]+/g, ' ').trim();
  if (!words) return raw;
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** First sentence (or first ~60 chars) of a free-text agent goal. */
export function firstSentence(goal: string, max = 64): string {
  const flat = goal.replace(/\s+/g, ' ').trim();
  const period = flat.indexOf('. ');
  const candidate = period > 0 && period < max + 20 ? flat.slice(0, period) : flat;
  return candidate.length > max ? candidate.slice(0, max - 1).trimEnd() + '…' : candidate;
}

/** Map a Bedrock model id / inference-profile id to a human-readable name. */
export function modelDisplayName(model: string): string {
  const m = model.toLowerCase();
  if (m.includes('haiku-4-5')) return 'Claude Haiku 4.5';
  if (m.includes('sonnet-4-6')) return 'Claude Sonnet 4.6';
  if (m.includes('sonnet-4-5')) return 'Claude Sonnet 4.5';
  if (m.includes('opus-4-7')) return 'Claude Opus 4.7';
  if (m.includes('opus-4-6')) return 'Claude Opus 4.6';
  if (m.includes('haiku')) return 'Claude Haiku';
  if (m.includes('sonnet')) return 'Claude Sonnet';
  if (m.includes('opus')) return 'Claude Opus';
  // Fallback: strip region prefix + provider, keep the readable tail.
  return model.replace(/^[a-z]+\./, '').replace(/^anthropic\./, '') || model;
}

const TRIGGER_LABELS: Record<string, { title: string; icon: string }> = {
  filesystem: { title: 'When a file arrives', icon: '📂' },
  schedule: { title: 'On a schedule', icon: '📅' },
  webhook: { title: 'When a webhook fires', icon: '🔗' },
  email: { title: 'When an email arrives', icon: '📥' },
  gmail_poll: { title: 'When an email arrives', icon: '📥' }, // legacy alias for email
  manual: { title: 'Manual / on demand', icon: '▶️' },
};

export function triggerTitle(type: string): string {
  return TRIGGER_LABELS[type]?.title ?? humanize(type);
}

export function triggerIcon(type: string): string {
  return TRIGGER_LABELS[type]?.icon ?? '⚡';
}

/** The one config detail a non-technical reader cares about for a trigger. */
export function triggerSubtitle(trigger: WorkflowDefinition['trigger']): string {
  if (!trigger) return '';
  const c = trigger.config ?? {};
  const pick = (k: string): string | undefined =>
    typeof c[k] === 'string' || typeof c[k] === 'number' ? String(c[k]) : undefined;
  switch (trigger.type) {
    case 'filesystem':
      return [pick('path'), pick('pattern')].filter(Boolean).join(' · ') || 'filesystem';
    case 'schedule':
      return pick('cron') ?? (pick('interval_seconds') ? `every ${pick('interval_seconds')}s` : 'schedule');
    case 'email':
    case 'gmail_poll': // legacy alias
      return pick('account') ?? pick('provider') ?? 'email';
    case 'webhook':
      return pick('trigger_id') ?? 'webhook';
    case 'manual':
      return 'no automatic trigger';
    default:
      return trigger.type;
  }
}

function stepTitle(step: WorkflowStep): string {
  // Author-set label wins; otherwise derive from goal / function name.
  if (step.label) return step.label;
  if (step.type === 'agentic') return firstSentence(step.goal);
  return humanize(step.function);
}

function stepSubtitle(step: WorkflowStep): string {
  if (step.type === 'agentic') return modelDisplayName(step.model);
  return step.function;
}

// ---- Layout ----

/**
 * Build positioned React Flow nodes + edges from a workflow definition.
 * A synthetic trigger node sits above the root steps (those with no incoming
 * edge). Layout is computed top-down with dagre; nodes are never hand-placed.
 */
export function buildGraph(def: WorkflowDefinition): {
  nodes: CanvasNode[];
  edges: CanvasEdge[];
} {
  const steps = def.steps ?? [];
  const edges = def.edges ?? [];

  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: 'TB', nodesep: 50, ranksep: 70, marginx: 20, marginy: 20 });
  g.setDefaultEdgeLabel(() => ({}));

  g.setNode(TRIGGER_NODE_ID, { width: NODE_WIDTH, height: NODE_HEIGHT });
  for (const s of steps) g.setNode(s.id, { width: NODE_WIDTH, height: NODE_HEIGHT });

  const hasIncoming = new Set(edges.map((e) => e.to));
  for (const s of steps) {
    if (!hasIncoming.has(s.id)) g.setEdge(TRIGGER_NODE_ID, s.id);
  }
  for (const e of edges) g.setEdge(e.from, e.to);

  dagre.layout(g);

  const toPosition = (id: string): { x: number; y: number } => {
    const n = g.node(id);
    // dagre reports node center; React Flow positions from the top-left.
    return { x: n.x - NODE_WIDTH / 2, y: n.y - NODE_HEIGHT / 2 };
  };

  const nodes: CanvasNode[] = [];
  nodes.push({
    id: TRIGGER_NODE_ID,
    type: 'trigger',
    position: toPosition(TRIGGER_NODE_ID),
    data: {
      kind: 'trigger',
      icon: triggerIcon(def.trigger?.type ?? ''),
      title: triggerTitle(def.trigger?.type ?? ''),
      subtitle: triggerSubtitle(def.trigger),
      trigger: def.trigger,
    },
  });
  for (const s of steps) {
    nodes.push({
      id: s.id,
      type: s.type,
      position: toPosition(s.id),
      data: {
        kind: s.type,
        icon: s.type === 'agentic' ? '🧠' : '⚙️',
        title: stepTitle(s),
        subtitle: stepSubtitle(s),
        step: s,
      },
    });
  }

  const rfEdges: CanvasEdge[] = [];
  const hasIncomingList = steps.filter((s) => !hasIncoming.has(s.id));
  for (const s of hasIncomingList) {
    rfEdges.push({ id: `${TRIGGER_NODE_ID}->${s.id}`, source: TRIGGER_NODE_ID, target: s.id });
  }
  for (const e of edges) {
    // Author-set `condition_label` reads in plain language; otherwise show
    // the raw expression. No label on unconditional edges.
    const label = e.condition_label ?? e.condition ?? undefined;
    rfEdges.push({
      id: `${e.from}->${e.to}`,
      source: e.from,
      target: e.to,
      ...(label ? { label } : {}),
    });
  }

  return { nodes, edges: rfEdges };
}

/** Narrowing helpers for the Inspector. */
export function isAgentic(step: WorkflowStep): step is AgenticStep {
  return step.type === 'agentic';
}
export function isDeterministic(step: WorkflowStep): step is DeterministicStep {
  return step.type === 'deterministic';
}

/** A step id unique against `existing`, based on a readable prefix. */
export function uniqueStepId(existing: string[], base: string): string {
  const set = new Set(existing);
  let n = existing.length + 1;
  let id = `${base}-${n}`;
  while (set.has(id)) {
    n += 1;
    id = `${base}-${n}`;
  }
  return id;
}

/** A new step with schema-valid defaults — what the palette inserts (C4). */
export function newStep(type: 'deterministic' | 'agentic', id: string): WorkflowStep {
  const common = { id, outputs: [], capabilities: null, runtime: { retries: 0, timeout_seconds: null } };
  if (type === 'agentic') {
    return {
      ...common,
      type: 'agentic',
      goal: 'Describe what this step should do.',
      tools: [],
      model: 'us.anthropic.claude-haiku-4-5-20251001-v1:0',
      system_prompt: null,
      policy: { max_iterations: 5, max_total_tokens: 4000, inference_config: null },
    };
  }
  return { ...common, type: 'deterministic', function: 'noop', config: {} };
}

export type { WorkflowEdge };
