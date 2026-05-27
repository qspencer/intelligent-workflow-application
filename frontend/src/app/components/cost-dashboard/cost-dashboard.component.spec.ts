import { computed, signal } from '@angular/core';
import { of, throwError } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';

import { ApiService } from '../../services/api.service';
import {
  CostRowByDay,
  CostRowByModel,
  CostRowByWorkflow,
} from '../../types';
import { CostDashboardComponent } from './cost-dashboard.component';

/**
 * Build a CostDashboardComponent instance without Angular's injection
 * context. `Object.create` skips field initializers (which include
 * `signal()` calls + `inject(ApiService)`), so we set them up
 * manually — same pattern as `api.service.spec.ts`, just with more
 * fields to wire.
 */
function makeComponent(api?: Partial<ApiService>): CostDashboardComponent {
  const fallback: Partial<ApiService> = {
    costByWorkflow: vi.fn(() => of([])),
    costByModel: vi.fn(() => of([])),
    costByDay: vi.fn(() => of([])),
  };
  const c = Object.create(CostDashboardComponent.prototype) as CostDashboardComponent;
  const fields = c as unknown as {
    api: Partial<ApiService>;
    window: 'all' | '24h' | '7d' | '30d';
    byWorkflow: ReturnType<typeof signal<CostRowByWorkflow[]>>;
    byModel: ReturnType<typeof signal<CostRowByModel[]>>;
    byDay: ReturnType<typeof signal<CostRowByDay[]>>;
    loading: ReturnType<typeof signal<boolean>>;
    error: ReturnType<typeof signal<string | null>>;
    totals: ReturnType<typeof computed<{ cost: number; tokens: number; steps: number }>>;
  };
  fields.api = { ...fallback, ...api };
  fields.window = 'all';
  fields.byWorkflow = signal<CostRowByWorkflow[]>([]);
  fields.byModel = signal<CostRowByModel[]>([]);
  fields.byDay = signal<CostRowByDay[]>([]);
  fields.loading = signal(true);
  fields.error = signal<string | null>(null);
  fields.totals = computed(() => {
    let cost = 0;
    let tokens = 0;
    let steps = 0;
    for (const r of c.byWorkflow()) {
      cost += r.total_cost_usd;
      tokens += r.total_tokens;
      steps += r.step_count;
    }
    return { cost, tokens, steps };
  });
  return c;
}

describe('CostDashboardComponent', () => {
  it('totals sum cost / tokens / steps across byWorkflow rows', () => {
    const c = makeComponent({
      costByWorkflow: vi.fn(() =>
        of([
          { workflow_id: 'a', total_cost_usd: 1.23, total_tokens: 1000, step_count: 5 },
          { workflow_id: 'b', total_cost_usd: 0.77, total_tokens: 500, step_count: 2 },
        ]),
      ),
    });
    c.refresh();
    expect(c.totals()).toEqual({ cost: 2.0, tokens: 1500, steps: 7 });
  });

  it.each([
    ['all', undefined],
    ['24h', 24],
    ['7d', 24 * 7],
    ['30d', 24 * 30],
  ] as const)(
    'window=%s passes a since param that is %s hours back',
    (window, expectedHoursBack) => {
      const costByWorkflowMock = vi.fn(() => of([]));
      const c = makeComponent({ costByWorkflow: costByWorkflowMock });
      c.window = window;
      c.refresh();
      const passedSince = costByWorkflowMock.mock.calls[0][0] as string | undefined;
      if (expectedHoursBack === undefined) {
        expect(passedSince).toBeUndefined();
      } else {
        expect(passedSince).toBeDefined();
        const sinceMs = new Date(passedSince!).getTime();
        const expectedMs = Date.now() - expectedHoursBack * 3_600_000;
        // Allow 1s slack for the time elapsed during the test itself.
        expect(Math.abs(sinceMs - expectedMs)).toBeLessThan(1000);
      }
    },
  );

  it('an error from one endpoint does not blank out the others', () => {
    const c = makeComponent({
      costByWorkflow: vi.fn(() => throwError(() => new Error('boom'))),
      costByModel: vi.fn(() =>
        of([{ model: 'haiku-4.5', total_cost_usd: 0.5, total_tokens: 100, step_count: 1 }]),
      ),
      costByDay: vi.fn(() =>
        of([{ date: '2026-05-27', total_cost_usd: 0.5, total_tokens: 100, step_count: 1 }]),
      ),
    });
    c.refresh();
    expect(c.error()).toMatch(/boom/);
    // Other tables still populated.
    expect(c.byModel().length).toBe(1);
    expect(c.byDay().length).toBe(1);
  });

  it('refresh on ngOnInit triggers all three API calls', () => {
    const w = vi.fn(() => of([]));
    const m = vi.fn(() => of([]));
    const d = vi.fn(() => of([]));
    const c = makeComponent({ costByWorkflow: w, costByModel: m, costByDay: d });
    c.ngOnInit();
    expect(w).toHaveBeenCalledTimes(1);
    expect(m).toHaveBeenCalledTimes(1);
    expect(d).toHaveBeenCalledTimes(1);
  });
});
