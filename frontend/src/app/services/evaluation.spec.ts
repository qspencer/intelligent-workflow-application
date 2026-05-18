import { describe, expect, it } from 'vitest';

import { StepExecution } from '../types';
import { extractEvaluations, scoreClass } from './evaluation';

function step(
  step_id: string,
  output: Record<string, unknown> | null,
): StepExecution {
  return {
    id: `exec-${step_id}`,
    instance_id: 'i-1',
    step_id,
    state: 'completed',
    output,
    error: null,
    started_at: null,
    completed_at: null,
  };
}

describe('extractEvaluations', () => {
  it('returns empty when no step has parse_ok in its output', () => {
    const steps = [
      step('extract', { text: 'INVOICE' }),
      step('classify', { output_text: 'invoice' }),
      step('route', { document_type: 'invoice' }),
    ];
    expect(extractEvaluations(steps)).toEqual([]);
  });

  it('extracts a full evaluation result', () => {
    const steps = [
      step('record_eval', {
        parse_ok: true,
        faithfulness_score: 4,
        category_score: 5,
        reasoning: 'Looks correct.',
        issues: ['minor: date inferred'],
      }),
    ];
    const out = extractEvaluations(steps);
    expect(out).toHaveLength(1);
    expect(out[0]).toEqual({
      step_id: 'record_eval',
      parse_ok: true,
      faithfulness_score: 4,
      category_score: 5,
      reasoning: 'Looks correct.',
      issues: ['minor: date inferred'],
    });
  });

  it('preserves parse_ok=false outputs with raw text', () => {
    const steps = [
      step('record_eval', { parse_ok: false, raw: 'not JSON, sorry' }),
    ];
    const out = extractEvaluations(steps);
    expect(out).toHaveLength(1);
    expect(out[0].parse_ok).toBe(false);
    expect(out[0].raw).toBe('not JSON, sorry');
    expect(out[0].faithfulness_score).toBeUndefined();
  });

  it('drops non-string entries from issues', () => {
    const steps = [
      step('record_eval', {
        parse_ok: true,
        issues: ['ok', 42, null, 'also ok'],
      }),
    ];
    const out = extractEvaluations(steps);
    expect(out[0].issues).toEqual(['ok', 'also ok']);
  });

  it('skips steps with null output', () => {
    expect(extractEvaluations([step('x', null)])).toEqual([]);
  });

  it('returns multiple evaluations when more than one step qualifies', () => {
    const steps = [
      step('eval_a', { parse_ok: true, faithfulness_score: 5 }),
      step('eval_b', { parse_ok: true, category_score: 2 }),
    ];
    const out = extractEvaluations(steps);
    expect(out.map((e) => e.step_id)).toEqual(['eval_a', 'eval_b']);
  });
});

describe('scoreClass', () => {
  it.each([
    [5, 'good'],
    [4, 'good'],
    [3, 'warn'],
    [2, 'err'],
    [1, 'err'],
    [0, 'err'],
  ] as const)('score=%d → %s', (score, expected) => {
    expect(scoreClass(score)).toBe(expected);
  });

  it('returns "unknown" when score is undefined', () => {
    expect(scoreClass(undefined)).toBe('unknown');
  });
});
