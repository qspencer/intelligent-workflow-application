/** Run-comparison helpers (G5: memory-hash diff view).
 *
 * Pure functions that align two runs of the same workflow step-by-step and
 * surface the facets that answer "why did this run behave differently?":
 * which rubric version (memory_hash) each step saw, what per-sender history
 * it recalled, and what it concluded.
 */

import { extractRecall, type StepRecall } from './memory';
import { extractUsage, type TokenUsage } from './usage';
import type { StepExecution } from '../types';

export interface StepFacet {
  state: string;
  memoryHash: string | null;
  recall: StepRecall | null;
  usage: TokenUsage | null;
  /** A triage-style category when the step recorded one (`parse_ok` true). */
  category: string | null;
  /** A short human signal: category summary, else output_text excerpt. */
  signal: string | null;
}

export interface CompareRow {
  stepId: string;
  a: StepFacet | null;
  b: StepFacet | null;
  hashDiffers: boolean;
  categoryDiffers: boolean;
}

function str(v: unknown): string | null {
  return typeof v === 'string' && v.length > 0 ? v : null;
}

export function stepFacet(step: StepExecution): StepFacet {
  const out = step.output ?? {};
  const parseOk = out['parse_ok'] === true;
  const category = parseOk ? str(out['category']) : null;
  const summary = parseOk ? str(out['summary']) : null;
  const text = str(out['output_text']);
  const excerpt = text ? (text.length > 120 ? `${text.slice(0, 120)}…` : text) : null;
  return {
    state: step.state,
    memoryHash: str(out['memory_hash']),
    recall: extractRecall(step),
    usage: extractUsage(step),
    category,
    signal: summary ?? excerpt,
  };
}

/** Align two runs' steps by step_id, preserving the first run's step order
 * (steps only in run B are appended). A facet is null when that run never
 * executed the step (skipped branches, earlier failure). */
export function compareRuns(a: StepExecution[], b: StepExecution[]): CompareRow[] {
  const byIdB = new Map(b.map((s) => [s.step_id, s]));
  const seen = new Set<string>();
  const rows: CompareRow[] = [];
  const push = (stepId: string, sa: StepExecution | null, sb: StepExecution | null): void => {
    const fa = sa ? stepFacet(sa) : null;
    const fb = sb ? stepFacet(sb) : null;
    rows.push({
      stepId,
      a: fa,
      b: fb,
      hashDiffers:
        fa?.memoryHash != null && fb?.memoryHash != null && fa.memoryHash !== fb.memoryHash,
      categoryDiffers:
        fa?.category != null && fb?.category != null && fa.category !== fb.category,
    });
  };
  for (const sa of a) {
    seen.add(sa.step_id);
    push(sa.step_id, sa, byIdB.get(sa.step_id) ?? null);
  }
  for (const sb of b) {
    if (!seen.has(sb.step_id)) push(sb.step_id, null, sb);
  }
  return rows;
}
