import { StepExecution } from '../types';

/**
 * Token + cost usage lifted from an agent step's output dict.
 *
 * Matches the engine's `_run_agentic` return shape:
 *   {
 *     "usage": {"input_tokens": ..., "output_tokens": ..., "iterations": ...},
 *     "cost_usd": ...,
 *     "model": ...
 *   }
 *
 * Returns null for deterministic steps (which carry no `usage` field) or
 * for any output shape that doesn't have at least input + output tokens.
 */
export interface TokenUsage {
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
  iterations: number;
  costUsd: number;
  model: string;
}

export function extractUsage(step: StepExecution): TokenUsage | null {
  const out = step.output;
  if (!out || typeof out !== 'object') return null;
  const usage = (out as Record<string, unknown>)['usage'];
  if (!usage || typeof usage !== 'object') return null;

  const u = usage as Record<string, unknown>;
  const input = typeof u['input_tokens'] === 'number' ? u['input_tokens'] : null;
  const output = typeof u['output_tokens'] === 'number' ? u['output_tokens'] : null;
  if (input === null || output === null) return null;

  const totalRaw = u['total_tokens'];
  const total = typeof totalRaw === 'number' ? totalRaw : input + output;
  const itersRaw = u['iterations'];
  const iterations = typeof itersRaw === 'number' ? itersRaw : 1;

  const costRaw = (out as Record<string, unknown>)['cost_usd'];
  const cost = typeof costRaw === 'number' ? costRaw : 0;
  const modelRaw = (out as Record<string, unknown>)['model'];
  const model = typeof modelRaw === 'string' ? modelRaw : '';

  return {
    inputTokens: input,
    outputTokens: output,
    totalTokens: total,
    iterations,
    costUsd: cost,
    model,
  };
}

/** Compact display string: `in: 1234 · out: 156 · $0.000123` */
export function formatUsage(u: TokenUsage): string {
  return `in: ${u.inputTokens} · out: ${u.outputTokens} · $${u.costUsd.toFixed(6)}`;
}

/** Tooltip text: model + iteration count when iterations > 1. */
export function usageTooltip(u: TokenUsage): string {
  const base = `${u.model} (total ${u.totalTokens} tokens)`;
  return u.iterations > 1 ? `${base}, ${u.iterations} iterations` : base;
}

/**
 * Bucket the cost split into a CSS class.
 *
 * Output tokens are ~5× the per-token price of input on Haiku 4.5
 * ($1/M in, $5/M out). When output tokens dominate the cost, the agent
 * is being unusually chatty — worth surfacing visually.
 */
export function costSkew(u: TokenUsage): 'output-heavy' | 'normal' {
  // Per-token output rate is 5× input on Haiku 4.5, so a balanced run
  // has input_tokens ≈ 5 × output_tokens. Output dominance starts when
  // output_tokens × 5 > input_tokens — i.e. the output share of cost
  // exceeds 50%.
  if (u.outputTokens === 0) return 'normal';
  return u.outputTokens * 5 > u.inputTokens ? 'output-heavy' : 'normal';
}
