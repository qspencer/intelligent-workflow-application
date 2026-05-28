import type { StepExecution } from '../types';

/**
 * Structured evaluation result extracted from a step's output.
 *
 * Mirrors the shape `record_evaluation` writes in the backend
 * (`backend/src/workflow_platform/engine/functions.py`):
 *   {parse_ok: true,  faithfulness_score, category_score, reasoning, issues}
 *   {parse_ok: false, raw}
 *
 * If neither shape matches, the step isn't an evaluation step.
 */
export interface EvaluationResult {
  step_id: string;
  parse_ok: boolean;
  faithfulness_score?: number;
  category_score?: number;
  reasoning?: string;
  issues?: string[];
  raw?: string;
}

/** Find steps whose output looks like a `record_evaluation` result. */
export function extractEvaluations(steps: StepExecution[]): EvaluationResult[] {
  const out: EvaluationResult[] = [];
  for (const step of steps) {
    const o = step.output;
    if (!o || typeof o !== 'object') continue;
    if (!('parse_ok' in o)) continue;
    out.push(toEvaluation(step.step_id, o as Record<string, unknown>));
  }
  return out;
}

function toEvaluation(
  step_id: string,
  output: Record<string, unknown>,
): EvaluationResult {
  const result: EvaluationResult = {
    step_id,
    parse_ok: output['parse_ok'] === true,
  };
  if (typeof output['faithfulness_score'] === 'number') {
    result.faithfulness_score = output['faithfulness_score'];
  }
  if (typeof output['category_score'] === 'number') {
    result.category_score = output['category_score'];
  }
  if (typeof output['reasoning'] === 'string') {
    result.reasoning = output['reasoning'];
  }
  if (Array.isArray(output['issues'])) {
    result.issues = output['issues'].filter(
      (x): x is string => typeof x === 'string',
    );
  }
  if (typeof output['raw'] === 'string') {
    result.raw = output['raw'];
  }
  return result;
}

/** Bucket a 0-5 score into a CSS class for color coding. */
export function scoreClass(
  score: number | undefined,
): 'good' | 'warn' | 'err' | 'unknown' {
  if (score === undefined) return 'unknown';
  if (score >= 4) return 'good';
  if (score >= 3) return 'warn';
  return 'err';
}
