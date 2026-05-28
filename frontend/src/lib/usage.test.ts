import { describe, expect, it } from 'vitest';

import { costSkew, extractUsage, formatUsage, usageTooltip } from './usage';
import type { StepExecution } from '../types';

function step(output: Record<string, unknown> | null): StepExecution {
  return {
    id: 'exec-1',
    instance_id: 'i-1',
    step_id: 'classify',
    state: 'completed',
    output,
    error: null,
    started_at: null,
    completed_at: null,
  };
}

describe('extractUsage', () => {
  it('lifts usage + cost + model from an agent step', () => {
    const u = extractUsage(
      step({
        usage: { input_tokens: 1000, output_tokens: 200, iterations: 2 },
        cost_usd: 0.0021,
        model: 'us.anthropic.claude-haiku-4-5-20251001-v1:0',
      }),
    );
    expect(u).toEqual({
      inputTokens: 1000,
      outputTokens: 200,
      totalTokens: 1200,
      iterations: 2,
      costUsd: 0.0021,
      model: 'us.anthropic.claude-haiku-4-5-20251001-v1:0',
    });
  });

  it('returns null for deterministic steps (no usage field)', () => {
    expect(extractUsage(step({ category: 'invoice' }))).toBeNull();
    expect(extractUsage(step(null))).toBeNull();
  });

  it('returns null when input/output tokens are missing', () => {
    expect(extractUsage(step({ usage: { input_tokens: 10 } }))).toBeNull();
  });

  it('defaults total to input+output and iterations to 1', () => {
    const u = extractUsage(step({ usage: { input_tokens: 5, output_tokens: 3 } }));
    expect(u?.totalTokens).toBe(8);
    expect(u?.iterations).toBe(1);
    expect(u?.costUsd).toBe(0);
    expect(u?.model).toBe('');
  });
});

describe('formatUsage / usageTooltip / costSkew', () => {
  const base = {
    inputTokens: 1000,
    outputTokens: 100,
    totalTokens: 1100,
    iterations: 1,
    costUsd: 0.0012,
    model: 'haiku',
  };

  it('formats a compact usage string', () => {
    expect(formatUsage(base)).toBe('in: 1000 · out: 100 · $0.001200');
  });

  it('omits iteration count when there is only one', () => {
    expect(usageTooltip(base)).toBe('haiku (total 1100 tokens)');
    expect(usageTooltip({ ...base, iterations: 3 })).toBe(
      'haiku (total 1100 tokens), 3 iterations',
    );
  });

  it('flags output-heavy runs', () => {
    expect(costSkew(base)).toBe('normal'); // 100*5 = 500 < 1000
    expect(costSkew({ ...base, outputTokens: 300 })).toBe('output-heavy'); // 1500 > 1000
    expect(costSkew({ ...base, outputTokens: 0 })).toBe('normal');
  });
});
