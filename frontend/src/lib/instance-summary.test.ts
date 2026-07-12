import { describe, expect, it } from 'vitest';

import { instanceSummary } from './instance-summary';
import type { WorkflowInstance } from '../types';

function inst(over: Partial<WorkflowInstance>): WorkflowInstance {
  return {
    id: 'i1',
    workflow_id: 'wf',
    state: 'completed',
    trigger_payload: {},
    error: null,
    created_at: '2026-07-12T12:00:00Z',
    started_at: null,
    completed_at: null,
    ...over,
  } as WorkflowInstance;
}

describe('instanceSummary', () => {
  it('lifts subject + recorded triage category', () => {
    const s = instanceSummary(
      inst({
        trigger_payload: { subject: 'Order Confirmation #123' },
        context: {
          steps: {
            triage: { output_text: '{"category":"fyi"}' }, // no parse_ok — ignored
            record: { parse_ok: true, category: 'fyi', confidence: 0.92 },
          },
        },
      }),
    );
    expect(s).toEqual({ subject: 'Order Confirmation #123', category: 'fyi' });
  });

  it('falls back to file_path as the subject (filesystem workflows)', () => {
    const s = instanceSummary(inst({ trigger_payload: { file_path: '/inbox/invoice.pdf' } }));
    expect(s.subject).toBe('/inbox/invoice.pdf');
    expect(s.category).toBeNull();
  });

  it('ignores unparsed records and handles missing context', () => {
    const s = instanceSummary(
      inst({ context: { steps: { record: { parse_ok: false, category: 'urgent' } } } }),
    );
    expect(s).toEqual({ subject: null, category: null });
    expect(instanceSummary(inst({}))).toEqual({ subject: null, category: null });
  });
});
