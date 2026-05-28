import { describe, expect, it } from 'vitest';

import {
  TRIGGER_NODE_ID,
  buildGraph,
  firstSentence,
  humanize,
  modelDisplayName,
  newStep,
  statusMeta,
  triggerSubtitle,
  triggerTitle,
  uniqueStepId,
} from './canvas';
import type { AgenticStep, StepState, WorkflowDefinition } from '../types';

describe('label helpers', () => {
  it('humanizes snake/kebab case', () => {
    expect(humanize('record_evaluation')).toBe('Record evaluation');
    expect(humanize('route-by-value')).toBe('Route by value');
    expect(humanize('')).toBe('');
  });

  it('takes the first sentence of a goal, truncating long ones', () => {
    expect(firstSentence('You are a classifier. Decide the type.')).toBe(
      'You are a classifier',
    );
    expect(firstSentence('a'.repeat(100)).endsWith('…')).toBe(true);
  });

  it('maps model ids to friendly names', () => {
    expect(modelDisplayName('us.anthropic.claude-haiku-4-5-20251001-v1:0')).toBe(
      'Claude Haiku 4.5',
    );
    expect(modelDisplayName('eu.anthropic.claude-sonnet-4-6-v1:0')).toBe(
      'Claude Sonnet 4.6',
    );
    expect(modelDisplayName('something-weird')).toBe('something-weird');
  });

  it('phrases triggers in plain language', () => {
    expect(triggerTitle('filesystem')).toBe('When a file arrives');
    expect(triggerTitle('gmail_poll')).toBe('When an email arrives');
    expect(triggerTitle('mystery')).toBe('Mystery');
    expect(
      triggerSubtitle({ type: 'filesystem', config: { path: './in', pattern: '*.pdf' } }),
    ).toBe('./in · *.pdf');
    expect(
      triggerSubtitle({ type: 'schedule', config: { interval_seconds: 60 } }),
    ).toBe('every 60s');
  });
});

describe('buildGraph', () => {
  const def: WorkflowDefinition = {
    id: 'pdf-classifier',
    name: 'PDF Classifier',
    description: '',
    trigger: { type: 'filesystem', config: { path: './in', pattern: '*.pdf' } },
    steps: [
      { id: 'extract', type: 'deterministic', function: 'pdf_extract', config: {}, outputs: [], capabilities: null, runtime: { retries: 0, timeout_seconds: null } },
      { id: 'classify', type: 'agentic', goal: 'Classify the document. Return JSON.', tools: [], model: 'us.anthropic.claude-haiku-4-5-20251001-v1:0', system_prompt: null, policy: { max_iterations: 2, max_total_tokens: 4000, inference_config: null }, outputs: [], capabilities: null, runtime: { retries: 0, timeout_seconds: null } },
      { id: 'route', type: 'deterministic', function: 'route_by_classification', config: {}, outputs: [], capabilities: null, runtime: { retries: 0, timeout_seconds: null } },
      { id: 'evaluate', type: 'agentic', goal: 'Score the classification.', tools: [], model: 'us.anthropic.claude-haiku-4-5-20251001-v1:0', system_prompt: null, policy: { max_iterations: 2, max_total_tokens: 4000, inference_config: null }, outputs: [], capabilities: null, runtime: { retries: 0, timeout_seconds: null } },
    ],
    edges: [
      { from: 'extract', to: 'classify', condition: null },
      { from: 'classify', to: 'route', condition: null },
      { from: 'classify', to: 'evaluate', condition: null },
    ],
  };

  it('emits a trigger node plus one node per step', () => {
    const { nodes } = buildGraph(def);
    expect(nodes).toHaveLength(5);
    expect(nodes.find((n) => n.id === TRIGGER_NODE_ID)?.type).toBe('trigger');
    expect(nodes.find((n) => n.id === 'classify')?.type).toBe('agentic');
    expect(nodes.find((n) => n.id === 'extract')?.type).toBe('deterministic');
  });

  it('derives friendly node titles', () => {
    const { nodes } = buildGraph(def);
    expect(nodes.find((n) => n.id === 'route')?.data.title).toBe('Route by classification');
    expect(nodes.find((n) => n.id === 'classify')?.data.subtitle).toBe('Claude Haiku 4.5');
  });

  it('connects the trigger to root steps and preserves declared edges', () => {
    const { edges } = buildGraph(def);
    // trigger→extract (root) + the 3 declared edges
    expect(edges).toHaveLength(4);
    expect(edges.some((e) => e.source === TRIGGER_NODE_ID && e.target === 'extract')).toBe(true);
    expect(edges.filter((e) => e.source === 'classify')).toHaveLength(2);
  });

  it('assigns every node a computed position', () => {
    const { nodes } = buildGraph(def);
    for (const n of nodes) {
      expect(Number.isFinite(n.position.x)).toBe(true);
      expect(Number.isFinite(n.position.y)).toBe(true);
    }
  });

  it('labels conditional edges with the expression', () => {
    const conditional: WorkflowDefinition = {
      ...def,
      edges: [{ from: 'extract', to: 'classify', condition: 'score > 0.8' }],
    };
    const { edges } = buildGraph(conditional);
    expect(edges.find((e) => e.source === 'extract')?.label).toBe('score > 0.8');
  });

  it('prefers author-set label / condition_label over derived text', () => {
    const labelled: WorkflowDefinition = {
      ...def,
      steps: def.steps!.map((s) =>
        s.id === 'classify' ? { ...s, label: 'Decide the category' } : s,
      ),
      edges: [
        { from: 'extract', to: 'classify', condition: 'score > 0.8', condition_label: 'High confidence' },
      ],
    };
    const { nodes, edges } = buildGraph(labelled);
    expect(nodes.find((n) => n.id === 'classify')?.data.title).toBe('Decide the category');
    expect(edges.find((e) => e.source === 'extract')?.label).toBe('High confidence');
  });
});

describe('uniqueStepId', () => {
  it('avoids collisions with existing ids', () => {
    expect(uniqueStepId([], 'step')).toBe('step-1');
    expect(uniqueStepId(['a', 'b'], 'step')).toBe('step-3');
    expect(uniqueStepId(['step-3'], 'step')).toBe('step-2');
    expect(uniqueStepId(['step-1', 'step-2'], 'step')).toBe('step-3');
  });
});

describe('newStep', () => {
  it('builds a schema-valid deterministic step', () => {
    const s = newStep('deterministic', 'step-1');
    expect(s.type).toBe('deterministic');
    expect(s.id).toBe('step-1');
    if (s.type === 'deterministic') {
      expect(s.function).toBe('noop');
      expect(s.config).toEqual({});
    }
    expect(s.runtime).toEqual({ retries: 0, timeout_seconds: null });
  });

  it('builds a schema-valid agentic step', () => {
    const s = newStep('agentic', 'ai-1') as AgenticStep;
    expect(s.type).toBe('agentic');
    expect(s.model).toContain('claude');
    expect(s.tools).toEqual([]);
    expect(s.policy.max_iterations).toBeGreaterThan(0);
  });
});

describe('statusMeta', () => {
  it('maps each step state to a plain-language label + css class', () => {
    const cases: Array<[StepState, string, string]> = [
      ['running', 'Running…', 'running'],
      ['completed', 'Done', 'completed'],
      ['failed', 'Failed', 'failed'],
      ['skipped', 'Skipped', 'skipped'],
      ['pending', 'Waiting', 'pending'],
    ];
    for (const [state, label, cssClass] of cases) {
      const meta = statusMeta(state);
      expect(meta.label).toBe(label);
      expect(meta.cssClass).toBe(cssClass);
      expect(meta.icon).toBeTruthy();
    }
  });
});
