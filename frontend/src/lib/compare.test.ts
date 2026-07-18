import { describe, expect, it } from 'vitest';

import { compareRuns, stepFacet } from './compare';
import type { StepExecution } from '../types';

function step(
  stepId: string,
  output: Record<string, unknown> | null,
  state = 'completed',
): StepExecution {
  return {
    id: `${stepId}-${state}`,
    instance_id: 'i',
    step_id: stepId,
    state,
    output,
    error: null,
    started_at: null,
    completed_at: null,
  } as StepExecution;
}

describe('stepFacet', () => {
  it('lifts hash, recall, category, and summary signal', () => {
    const f = stepFacet(
      step('triage', {
        memory_hash: 'sha256:aaaa',
        recall: { query: 'x@y.z', edges: 5, episodes: 2, context_hash: 'sha256:bb' },
        parse_ok: true,
        category: 'promotion',
        summary: 'Seasonal offer.',
      }),
    );
    expect(f.memoryHash).toBe('sha256:aaaa');
    expect(f.recall?.edges).toBe(5);
    expect(f.category).toBe('promotion');
    expect(f.signal).toBe('Seasonal offer.');
  });

  it('falls back to an output_text excerpt when no parsed verdict', () => {
    const f = stepFacet(step('triage', { output_text: 'x'.repeat(200) }));
    expect(f.category).toBeNull();
    expect(f.signal?.length).toBe(121); // 120 + ellipsis
  });
});

describe('compareRuns', () => {
  it('aligns by step_id and flags hash + category differences', () => {
    const a = [
      step('triage', { memory_hash: 'sha256:old', parse_ok: true, category: 'spam' }),
      step('record', { parse_ok: true, category: 'spam' }),
    ];
    const b = [
      step('triage', { memory_hash: 'sha256:new', parse_ok: true, category: 'urgent' }),
      step('record', { parse_ok: true, category: 'urgent' }),
    ];
    const rows = compareRuns(a, b);
    expect(rows).toHaveLength(2);
    expect(rows[0].hashDiffers).toBe(true);
    expect(rows[0].categoryDiffers).toBe(true);
    // record step carries no memory_hash — same-category diff logic only.
    expect(rows[1].hashDiffers).toBe(false);
    expect(rows[1].categoryDiffers).toBe(true);
  });

  it('handles steps missing from one run (skips, early failures)', () => {
    const rows = compareRuns([step('a', {}), step('b', {})], [step('b', {}), step('c', {})]);
    expect(rows.map((r) => r.stepId)).toEqual(['a', 'b', 'c']);
    expect(rows[0].b).toBeNull();
    expect(rows[2].a).toBeNull();
  });

  it('never flags a diff when either side lacks the facet', () => {
    const rows = compareRuns(
      [step('t', { memory_hash: 'sha256:x' })],
      [step('t', {})],
    );
    expect(rows[0].hashDiffers).toBe(false);
  });
});
