import { describe, expect, it } from 'vitest';

import { extractTriage } from './triage';

describe('extractTriage', () => {
  it('pulls PR-triage fields', () => {
    const t = extractTriage({
      category: 'bugfix',
      complexity: 'moderate',
      needs_tests: true,
      summary: 'Fixes a null deref',
      concerns: ['no test coverage'],
      concern_count: 1,
    });
    expect(t?.headline).toBe('bugfix');
    expect(t?.complexity).toBe('moderate');
    expect(t?.summary).toBe('Fixes a null deref');
    expect(t?.chips).toEqual([{ label: 'Concerns', items: ['no test coverage'] }]);
    expect(t?.flags).toEqual([{ label: 'needs tests', value: true }]);
  });

  it('pulls paper-triage fields (relevance + tags)', () => {
    const t = extractTriage({
      relevance_score: 4,
      relevance_bucket: 'directly_relevant',
      summary: 'A memory paper',
      key_concepts: ['consolidation', 'retrieval'],
      tags: ['agent_memory'],
    });
    expect(t?.headline).toBe('directly_relevant');
    expect(t?.score).toBe(4);
    expect(t?.chips.find((c) => c.label === 'Tags')?.items).toEqual(['agent_memory']);
    expect(t?.chips.find((c) => c.label === 'Key concepts')?.items).toHaveLength(2);
  });

  it('pulls email-triage fields (confidence + labels + reply flag)', () => {
    const t = extractTriage({
      category: 'urgent',
      confidence: 0.92,
      reply_drafted: false,
      labels_applied: ['triaged/urgent'],
      summary: 'Final notice',
    });
    expect(t?.headline).toBe('urgent');
    expect(t?.confidence).toBe(0.92);
    expect(t?.flags).toEqual([{ label: 'reply drafted', value: false }]);
  });

  it('returns null for non-triage output', () => {
    expect(extractTriage({ usage: { input_tokens: 1 }, cost_usd: 0.01 })).toBeNull();
    expect(extractTriage(null)).toBeNull();
    expect(extractTriage({ output_text: 'hello' })).toBeNull();
  });
});
