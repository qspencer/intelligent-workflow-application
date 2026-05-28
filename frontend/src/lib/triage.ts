/**
 * Generic "triage result" extraction for the canvas output card.
 *
 * The triage workflows (`record_pr_triage` / `record_paper_triage` /
 * `record_email_triage`) write a family of overlapping fields. Rather than a
 * bespoke card per workflow, this pulls whatever common fields are present so
 * one card renders all of them:
 *   - headline  ← category | relevance_bucket
 *   - score     ← relevance_score   (0–5)
 *   - complexity← complexity        (string tier)
 *   - confidence← confidence        (0–1)
 *   - summary   ← summary
 *   - chips     ← tags | key_concepts | concerns | labels_applied
 *   - flags     ← needs_tests | reply_drafted   (booleans)
 *
 * Returns null when the output doesn't look like a triage result.
 */
export interface TriageCard {
  headline?: string;
  score?: number;
  complexity?: string;
  confidence?: number;
  summary?: string;
  chips: { label: string; items: string[] }[];
  flags: { label: string; value: boolean }[];
}

function strArray(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [];
}

export function extractTriage(output: Record<string, unknown> | null): TriageCard | null {
  if (!output || typeof output !== 'object') return null;
  const o = output;

  const headline =
    typeof o['category'] === 'string'
      ? o['category']
      : typeof o['relevance_bucket'] === 'string'
        ? o['relevance_bucket']
        : undefined;
  const score = typeof o['relevance_score'] === 'number' ? o['relevance_score'] : undefined;
  const complexity = typeof o['complexity'] === 'string' ? o['complexity'] : undefined;
  const confidence = typeof o['confidence'] === 'number' ? o['confidence'] : undefined;
  const summary = typeof o['summary'] === 'string' ? o['summary'] : undefined;

  const chipSpecs: [string, string][] = [
    ['tags', 'Tags'],
    ['key_concepts', 'Key concepts'],
    ['concerns', 'Concerns'],
    ['labels_applied', 'Labels'],
  ];
  const chips = chipSpecs
    .map(([key, label]) => ({ label, items: strArray(o[key]) }))
    .filter((c) => c.items.length > 0);

  const flagSpecs: [string, string][] = [
    ['needs_tests', 'needs tests'],
    ['reply_drafted', 'reply drafted'],
  ];
  const flags = flagSpecs
    .filter(([key]) => typeof o[key] === 'boolean')
    .map(([key, label]) => ({ label, value: o[key] === true }));

  const empty =
    headline === undefined &&
    score === undefined &&
    summary === undefined &&
    chips.length === 0 &&
    flags.length === 0;
  if (empty) return null;

  return { headline, score, complexity, confidence, summary, chips, flags };
}
