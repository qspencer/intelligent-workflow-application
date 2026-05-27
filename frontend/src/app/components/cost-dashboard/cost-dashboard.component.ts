import { CommonModule } from '@angular/common';
import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ApiService } from '../../services/api.service';
import {
  CostRowByDay,
  CostRowByModel,
  CostRowByWorkflow,
} from '../../types';

/**
 * Cost dashboard.
 *
 * Renders three side-by-side tables backed by `/api/cost/by-{workflow,model,day}`.
 * All three share an optional `since` filter (ISO date) that the user
 * can change via a single dropdown — "All time", "Last 24h", "Last 7d",
 * "Last 30d". The filter value translates into the `since` query
 * parameter on each request.
 *
 * No charts. Tables match the existing UI's visual language. If a
 * future workload calls for trend visualization, the by-day table is
 * the obvious chart target.
 */
@Component({
  selector: 'wp-cost-dashboard',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <h2>Cost Dashboard</h2>

    <div class="filter">
      <label for="since">Time range:</label>
      <select id="since" [(ngModel)]="window" (ngModelChange)="refresh()">
        <option value="all">All time</option>
        <option value="24h">Last 24 hours</option>
        <option value="7d">Last 7 days</option>
        <option value="30d">Last 30 days</option>
      </select>
      @if (loading()) {
        <span class="muted">Loading…</span>
      } @else if (error()) {
        <span class="error">{{ error() }}</span>
      } @else {
        <span class="muted">
          Totals across the selected window:
          <strong>{{ totals().cost | currency: 'USD' : 'symbol' : '1.4-4' }}</strong>
          /
          <strong>{{ totals().tokens | number }}</strong> tokens
          /
          <strong>{{ totals().steps | number }}</strong> agent steps
        </span>
      }
    </div>

    <section class="panel">
      <h3>By workflow</h3>
      @if (byWorkflow().length === 0) {
        <p class="muted">No agentic step executions in this window.</p>
      } @else {
        <table>
          <thead>
            <tr>
              <th>Workflow</th>
              <th class="num">Cost (USD)</th>
              <th class="num">Tokens</th>
              <th class="num">Steps</th>
            </tr>
          </thead>
          <tbody>
            @for (r of byWorkflow(); track r.workflow_id) {
              <tr>
                <td><code>{{ r.workflow_id }}</code></td>
                <td class="num">{{ r.total_cost_usd | currency: 'USD' : 'symbol' : '1.4-4' }}</td>
                <td class="num">{{ r.total_tokens | number }}</td>
                <td class="num">{{ r.step_count | number }}</td>
              </tr>
            }
          </tbody>
        </table>
      }
    </section>

    <section class="panel">
      <h3>By model</h3>
      @if (byModel().length === 0) {
        <p class="muted">No agentic step executions in this window.</p>
      } @else {
        <table>
          <thead>
            <tr>
              <th>Model</th>
              <th class="num">Cost (USD)</th>
              <th class="num">Tokens</th>
              <th class="num">Steps</th>
            </tr>
          </thead>
          <tbody>
            @for (r of byModel(); track r.model) {
              <tr>
                <td><code>{{ r.model }}</code></td>
                <td class="num">{{ r.total_cost_usd | currency: 'USD' : 'symbol' : '1.4-4' }}</td>
                <td class="num">{{ r.total_tokens | number }}</td>
                <td class="num">{{ r.step_count | number }}</td>
              </tr>
            }
          </tbody>
        </table>
      }
    </section>

    <section class="panel">
      <h3>By day</h3>
      @if (byDay().length === 0) {
        <p class="muted">No agentic step executions in this window.</p>
      } @else {
        <table>
          <thead>
            <tr>
              <th>Date</th>
              <th class="num">Cost (USD)</th>
              <th class="num">Tokens</th>
              <th class="num">Steps</th>
            </tr>
          </thead>
          <tbody>
            @for (r of byDay(); track r.date) {
              <tr>
                <td>{{ r.date }}</td>
                <td class="num">{{ r.total_cost_usd | currency: 'USD' : 'symbol' : '1.4-4' }}</td>
                <td class="num">{{ r.total_tokens | number }}</td>
                <td class="num">{{ r.step_count | number }}</td>
              </tr>
            }
          </tbody>
        </table>
      }
    </section>
  `,
  styles: [
    `
      .filter {
        display: flex;
        align-items: center;
        gap: 12px;
        margin-bottom: 16px;
      }
      .filter select {
        padding: 4px 8px;
      }
      .muted {
        color: var(--muted);
        font-size: 13px;
      }
      .error {
        color: var(--err);
      }
      .panel {
        margin-top: 24px;
      }
      .panel h3 {
        margin: 0 0 8px;
        font-size: 14px;
        font-weight: 600;
      }
      table {
        width: 100%;
        border-collapse: collapse;
      }
      th,
      td {
        border-bottom: 1px solid var(--border);
        padding: 6px 8px;
        text-align: left;
      }
      th.num,
      td.num {
        text-align: right;
        font-variant-numeric: tabular-nums;
      }
      code {
        font-size: 12px;
      }
    `,
  ],
})
export class CostDashboardComponent implements OnInit {
  private readonly api = inject(ApiService);

  /** UI-bound window selection. Translates to `since` ISO string. */
  window: 'all' | '24h' | '7d' | '30d' = 'all';

  readonly byWorkflow = signal<CostRowByWorkflow[]>([]);
  readonly byModel = signal<CostRowByModel[]>([]);
  readonly byDay = signal<CostRowByDay[]>([]);
  readonly loading = signal(true);
  readonly error = signal<string | null>(null);

  /** Aggregate totals across whatever is currently in `byWorkflow`. */
  readonly totals = computed(() => {
    let cost = 0;
    let tokens = 0;
    let steps = 0;
    for (const r of this.byWorkflow()) {
      cost += r.total_cost_usd;
      tokens += r.total_tokens;
      steps += r.step_count;
    }
    return { cost, tokens, steps };
  });

  ngOnInit(): void {
    this.refresh();
  }

  refresh(): void {
    const since = this.sinceParam();
    this.loading.set(true);
    this.error.set(null);

    // Fire the three calls in parallel; settle each independently so a
    // single backend error doesn't blank out the whole page.
    let outstanding = 3;
    const done = () => {
      outstanding -= 1;
      if (outstanding === 0) this.loading.set(false);
    };

    this.api.costByWorkflow(since).subscribe({
      next: (rows) => this.byWorkflow.set(rows),
      error: (e) => this.handleError(e),
      complete: done,
    });
    this.api.costByModel(since).subscribe({
      next: (rows) => this.byModel.set(rows),
      error: (e) => this.handleError(e),
      complete: done,
    });
    this.api.costByDay(since).subscribe({
      next: (rows) => this.byDay.set(rows),
      error: (e) => this.handleError(e),
      complete: done,
    });
  }

  /** Translate the UI's `window` value into an ISO `since` string. */
  private sinceParam(): string | undefined {
    if (this.window === 'all') return undefined;
    const ms = { '24h': 24, '7d': 24 * 7, '30d': 24 * 30 }[this.window] * 3_600_000;
    return new Date(Date.now() - ms).toISOString();
  }

  private handleError(e: { message?: string }): void {
    this.error.set(e?.message ?? 'Failed to load cost report');
    this.loading.set(false);
  }
}
