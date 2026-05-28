import { describe, expect, it } from 'vitest';

import { extractEvaluations, scoreClass } from './evaluation';
import type { StepExecution } from '../types';

function step(id: string, output: Record<string, unknown> | null): StepExecution {
  return {
    id: `exec-${id}`,
    instance_id: 'i-1',
    step_id: id,
    state: 'completed',
    output,
    error: null,
    started_at: null,
    completed_at: null,
  };
}

describe('extractEvaluations', () => {
  it('pulls a parsed evaluation result from a record_evaluation step', () => {
    const steps = [
      step('classify', { document_type: 'invoice' }),
      step('evaluate', {
        parse_ok: true,
        faithfulness_score: 5,
        category_score: 4,
        reasoning: 'matches',
        issues: ['minor wording'],
      }),
    ];
    const result = extractEvaluations(steps);
    expect(result).toHaveLength(1);
    expect(result[0]).toEqual({
      step_id: 'evaluate',
      parse_ok: true,
      faithfulness_score: 5,
      category_score: 4,
      reasoning: 'matches',
      issues: ['minor wording'],
    });
  });

  it('keeps the raw text when parse_ok is false', () => {
    const result = extractEvaluations([
      step('evaluate', { parse_ok: false, raw: 'not json' }),
    ]);
    expect(result[0]).toEqual({ step_id: 'evaluate', parse_ok: false, raw: 'not json' });
  });

  it('ignores steps without a parse_ok key and null outputs', () => {
    expect(
      extractEvaluations([step('a', { foo: 1 }), step('b', null)]),
    ).toHaveLength(0);
  });

  it('filters non-string entries out of issues', () => {
    const result = extractEvaluations([
      step('evaluate', { parse_ok: true, issues: ['ok', 3, null, 'two'] }),
    ]);
    expect(result[0].issues).toEqual(['ok', 'two']);
  });
});

describe('scoreClass', () => {
  it('buckets scores by threshold', () => {
    expect(scoreClass(undefined)).toBe('unknown');
    expect(scoreClass(5)).toBe('good');
    expect(scoreClass(4)).toBe('good');
    expect(scoreClass(3)).toBe('warn');
    expect(scoreClass(2)).toBe('err');
    expect(scoreClass(0)).toBe('err');
  });
});
