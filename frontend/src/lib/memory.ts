/** Learned-memory display helpers (veracium integration).
 *
 * Pure functions over step outputs and audit-entry details — the UI-side
 * companions to the engine's `memory_recalled` / `memory_observed` /
 * `memory_outcome_recorded` audit actions and the `recall` field on agentic
 * step output.
 */

import type { AuditEntry, StepExecution } from '../types';

/** The `recall` block on an agentic step's output (G10). */
export interface StepRecall {
  query: string;
  edges: number;
  episodes: number;
  contextHash: string | null;
}

export function extractRecall(step: StepExecution): StepRecall | null {
  const raw = step.output?.['recall'];
  if (raw == null || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  if (typeof r['query'] !== 'string') return null;
  return {
    query: r['query'],
    edges: typeof r['edges'] === 'number' ? r['edges'] : 0,
    episodes: typeof r['episodes'] === 'number' ? r['episodes'] : 0,
    contextHash: typeof r['context_hash'] === 'string' ? r['context_hash'] : null,
  };
}

export interface MemoryActivity {
  /** memory_recalled entries: what history the run consulted. */
  recalls: { query: string; edges: number; episodes: number; injected: boolean }[];
  /** memory_observed entries rolled up: what the run wrote back. */
  observed: { writes: number; facts: number; quarantined: number; costUsd: number };
  /** memory_outcome_recorded entries (e.g. fork corrections). */
  outcomes: { emitter: string; outcome: string; correctedValue: string | null }[];
  failures: number;
}

function num(v: unknown): number {
  return typeof v === 'number' && Number.isFinite(v) ? v : 0;
}

/** Roll a run's memory-related audit entries into one displayable summary.
 * Returns null when the run had no memory activity at all, so the panel
 * only renders for memory-enabled workflows. */
export function summarizeMemory(entries: AuditEntry[]): MemoryActivity | null {
  const activity: MemoryActivity = {
    recalls: [],
    observed: { writes: 0, facts: 0, quarantined: 0, costUsd: 0 },
    outcomes: [],
    failures: 0,
  };
  let any = false;
  for (const e of entries) {
    const d = e.detail ?? {};
    if (e.action === 'memory_recalled') {
      any = true;
      activity.recalls.push({
        query: typeof d['query'] === 'string' ? d['query'] : '',
        edges: num(d['edges']),
        episodes: num(d['episodes']),
        injected: d['injected'] === true,
      });
    } else if (e.action === 'memory_observed') {
      any = true;
      activity.observed.writes += 1;
      activity.observed.facts += num(d['facts']);
      activity.observed.quarantined += num(d['quarantined']);
      activity.observed.costUsd += num(d['cost_usd']);
    } else if (e.action === 'memory_outcome_recorded') {
      any = true;
      activity.outcomes.push({
        emitter: typeof d['emitter'] === 'string' ? d['emitter'] : '?',
        outcome: typeof d['outcome'] === 'string' ? d['outcome'] : '?',
        correctedValue:
          typeof d['corrected_value'] === 'string' ? d['corrected_value'] : null,
      });
    } else if (e.action === 'memory_recall_failed' || e.action === 'memory_observe_failed') {
      any = true;
      activity.failures += 1;
    }
  }
  return any ? activity : null;
}

/** The seven-bucket triage taxonomy → badge modifier class. Unknown values
 * (historical five-bucket verdicts, other workloads) fall back to the plain
 * neutral badge. */
const CATEGORY_CLASSES = new Set([
  'urgent',
  'awaiting-reply',
  'personal',
  'notification',
  'newsletter',
  'promotion',
  'spam',
]);

export function categoryClass(category: string): string {
  return CATEGORY_CLASSES.has(category) ? `cat-${category}` : '';
}
