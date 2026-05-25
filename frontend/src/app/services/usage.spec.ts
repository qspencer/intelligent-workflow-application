import { describe, expect, it } from 'vitest';

import { StepExecution } from '../types';
import { costSkew, extractUsage, formatUsage, usageTooltip } from './usage';

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

describe('extractUsage', () => {
  it('returns the full TokenUsage for a normal agentic step', () => {
    const u = extractUsage(
      step('classify', {
        usage: {
          input_tokens: 1234,
          output_tokens: 156,
          total_tokens: 1390,
          iterations: 1,
        },
        cost_usd: 0.000789,
        model: 'us.anthropic.claude-haiku-4-5-20251001-v1:0',
      }),
    );
    expect(u).toEqual({
      inputTokens: 1234,
      outputTokens: 156,
      totalTokens: 1390,
      iterations: 1,
      costUsd: 0.000789,
      model: 'us.anthropic.claude-haiku-4-5-20251001-v1:0',
    });
  });

  it('synthesizes total_tokens when missing', () => {
    const u = extractUsage(
      step('a', { usage: { input_tokens: 100, output_tokens: 25 } }),
    );
    expect(u?.totalTokens).toBe(125);
  });

  it('defaults iterations to 1 when absent', () => {
    const u = extractUsage(
      step('a', { usage: { input_tokens: 1, output_tokens: 1 } }),
    );
    expect(u?.iterations).toBe(1);
  });

  it('returns null for a deterministic step (no usage field)', () => {
    expect(
      extractUsage(step('route', { document_type: 'invoice', bytes_copied: 800 })),
    ).toBeNull();
  });

  it('returns null when output is missing', () => {
    expect(extractUsage(step('x', null))).toBeNull();
  });

  it('returns null when input/output tokens are not numbers', () => {
    expect(
      extractUsage(step('x', { usage: { input_tokens: '1234', output_tokens: 156 } })),
    ).toBeNull();
  });
});

describe('formatUsage', () => {
  it('renders the compact in/out/cost string', () => {
    expect(
      formatUsage({
        inputTokens: 1234,
        outputTokens: 156,
        totalTokens: 1390,
        iterations: 1,
        costUsd: 0.000789,
        model: 'model-x',
      }),
    ).toBe('in: 1234 · out: 156 · $0.000789');
  });

  it('pads cost to 6 decimal places', () => {
    expect(
      formatUsage({
        inputTokens: 0,
        outputTokens: 0,
        totalTokens: 0,
        iterations: 1,
        costUsd: 0.001,
        model: '',
      }),
    ).toBe('in: 0 · out: 0 · $0.001000');
  });
});

describe('usageTooltip', () => {
  it('shows model + total tokens for single-iteration runs', () => {
    expect(
      usageTooltip({
        inputTokens: 1,
        outputTokens: 1,
        totalTokens: 2,
        iterations: 1,
        costUsd: 0,
        model: 'claude-haiku',
      }),
    ).toBe('claude-haiku (total 2 tokens)');
  });

  it('adds iteration count when iterations > 1', () => {
    expect(
      usageTooltip({
        inputTokens: 1,
        outputTokens: 1,
        totalTokens: 2,
        iterations: 3,
        costUsd: 0,
        model: 'm',
      }),
    ).toBe('m (total 2 tokens), 3 iterations');
  });
});

describe('costSkew', () => {
  // Per the Haiku 4.5 pricing ratio: output token is 5× input price.
  // Output dominates cost when output_tokens × 5 > input_tokens.

  it('returns normal for a typical run (lots of input, little output)', () => {
    expect(
      costSkew({
        inputTokens: 1000,
        outputTokens: 50,
        totalTokens: 1050,
        iterations: 1,
        costUsd: 0.001,
        model: '',
      }),
    ).toBe('normal');
  });

  it('returns output-heavy when output cost dominates', () => {
    expect(
      costSkew({
        inputTokens: 100,
        outputTokens: 200,
        totalTokens: 300,
        iterations: 1,
        costUsd: 0.001,
        model: '',
      }),
    ).toBe('output-heavy');
  });

  it('handles zero output cleanly', () => {
    expect(
      costSkew({
        inputTokens: 100,
        outputTokens: 0,
        totalTokens: 100,
        iterations: 1,
        costUsd: 0.0001,
        model: '',
      }),
    ).toBe('normal');
  });
});
