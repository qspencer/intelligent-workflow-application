import { describe, expect, it } from 'vitest';

import { categoryClass, extractRecall, summarizeMemory } from './memory';
import type { AuditEntry, StepExecution } from '../types';

function step(output: Record<string, unknown> | null): StepExecution {
  return {
    id: 's1',
    instance_id: 'i1',
    step_id: 'triage',
    state: 'completed',
    output,
    error: null,
    started_at: null,
    completed_at: null,
  } as StepExecution;
}

function entry(action: string, detail: Record<string, unknown>): AuditEntry {
  return {
    id: Math.random().toString(36).slice(2),
    timestamp: '2026-07-19T00:00:00Z',
    actor_type: 'engine',
    actor_id: 'learned_memory',
    action,
    workflow_instance_id: 'i1',
    step_id: null,
    detail,
  } as AuditEntry;
}

describe('extractRecall', () => {
  it('lifts the recall block from agentic step output', () => {
    const r = extractRecall(
      step({
        recall: {
          query: 'promo@vendor.com',
          edges: 40,
          episodes: 12,
          context_hash: 'sha256:abc',
        },
      }),
    );
    expect(r).toEqual({
      query: 'promo@vendor.com',
      edges: 40,
      episodes: 12,
      contextHash: 'sha256:abc',
    });
  });

  it('returns null for steps without recall (deterministic, null recall)', () => {
    expect(extractRecall(step({ recall: null }))).toBeNull();
    expect(extractRecall(step({}))).toBeNull();
    expect(extractRecall(step(null))).toBeNull();
  });
});

describe('summarizeMemory', () => {
  it('returns null when the run had no memory activity', () => {
    expect(summarizeMemory([entry('workflow_started', {})])).toBeNull();
  });

  it('rolls recalls, observations, outcomes, and failures together', () => {
    const activity = summarizeMemory([
      entry('memory_recalled', {
        query: 'a@b.c',
        edges: 3,
        episodes: 2,
        injected: true,
      }),
      entry('memory_observed', { facts: 2, quarantined: 1, cost_usd: 0.003 }),
      entry('memory_observed', { facts: 1, quarantined: 0, cost_usd: 0.002 }),
      entry('memory_outcome_recorded', {
        emitter: 'fork',
        outcome: 'corrected',
        corrected_value: 'urgent',
      }),
      entry('memory_recall_failed', { error: 'boom' }),
    ]);
    expect(activity).not.toBeNull();
    expect(activity?.recalls).toEqual([
      { query: 'a@b.c', edges: 3, episodes: 2, injected: true },
    ]);
    expect(activity?.observed).toEqual({
      writes: 2,
      facts: 3,
      quarantined: 1,
      costUsd: 0.005,
    });
    expect(activity?.outcomes).toEqual([
      { emitter: 'fork', outcome: 'corrected', correctedValue: 'urgent' },
    ]);
    expect(activity?.failures).toBe(1);
  });
});

describe('categoryClass', () => {
  it('maps the seven-bucket taxonomy to modifier classes', () => {
    expect(categoryClass('promotion')).toBe('cat-promotion');
    expect(categoryClass('awaiting-reply')).toBe('cat-awaiting-reply');
  });

  it('falls back to no modifier for unknown/historical categories', () => {
    expect(categoryClass('fyi')).toBe('');
    expect(categoryClass('weird')).toBe('');
  });
});
