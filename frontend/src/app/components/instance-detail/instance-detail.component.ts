import { CommonModule, DatePipe } from '@angular/common';
import { Component, computed, inject, Input, OnDestroy, OnInit, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { Subject, interval, takeUntil } from 'rxjs';

import { ApiService } from '../../services/api.service';
import {
  EvaluationResult,
  extractEvaluations,
  scoreClass,
} from '../../services/evaluation';
import { EventsService } from '../../services/events.service';
import {
  TokenUsage,
  costSkew,
  extractUsage,
  formatUsage,
  usageTooltip,
} from '../../services/usage';
import { AuditEntry, InstanceDetail, StepExecution, WorkflowInstance } from '../../types';

@Component({
  selector: 'wp-instance-detail',
  standalone: true,
  imports: [CommonModule, DatePipe, RouterLink],
  template: `
    <p><a routerLink="/instances">← All instances</a></p>

    @if (loading()) {
      <p>Loading…</p>
    } @else if (error()) {
      <p class="error">{{ error() }}</p>
    } @else if (instance()) {
      @let inst = instance()!;
      <h2>
        Instance <code>{{ short(inst.id) }}</code>
        <span class="badge" [class]="'badge ' + inst.state">{{ inst.state }}</span>
      </h2>

      <div class="meta">
        <div><strong>Workflow:</strong> {{ inst.workflow_id }}</div>
        <div><strong>Created:</strong> {{ inst.created_at | date: 'medium' }}</div>
        @if (inst.started_at) {
          <div><strong>Started:</strong> {{ inst.started_at | date: 'medium' }}</div>
        }
        @if (inst.completed_at) {
          <div><strong>Finished:</strong> {{ inst.completed_at | date: 'medium' }}</div>
        }
        @if (inst.error) {
          <div class="error"><strong>Error:</strong> {{ inst.error }}</div>
        }
      </div>

      <div class="actions">
        @if (inst.state === 'running') {
          <button (click)="action('pause')">Pause</button>
          <button class="danger" (click)="action('kill')">Kill</button>
        }
        @if (inst.state === 'paused') {
          <button (click)="action('resume')">Resume</button>
          <button class="danger" (click)="action('kill')">Kill</button>
        }
        @if (inst.state === 'failed') {
          <button (click)="action('retry')">Retry</button>
        }
      </div>

      @if (evaluations().length > 0) {
        <h3>Evaluation</h3>
        @for (e of evaluations(); track e.step_id) {
          <div class="eval">
            <div class="eval-head">
              <span class="step">{{ e.step_id }}</span>
              @if (!e.parse_ok) {
                <span class="badge failed">parse failed</span>
              }
            </div>
            @if (e.parse_ok) {
              <div class="scores">
                @if (e.faithfulness_score !== undefined) {
                  <div class="score">
                    <span class="label">Faithfulness</span>
                    <span class="value" [class]="'value ' + scoreClass(e.faithfulness_score)">
                      {{ e.faithfulness_score }} / 5
                    </span>
                  </div>
                }
                @if (e.category_score !== undefined) {
                  <div class="score">
                    <span class="label">Category</span>
                    <span class="value" [class]="'value ' + scoreClass(e.category_score)">
                      {{ e.category_score }} / 5
                    </span>
                  </div>
                }
              </div>
              @if (e.reasoning) {
                <div class="reasoning">{{ e.reasoning }}</div>
              }
              @if (e.issues && e.issues.length > 0) {
                <ul class="issues">
                  @for (issue of e.issues; track issue) {
                    <li>{{ issue }}</li>
                  }
                </ul>
              }
            } @else if (e.raw) {
              <pre class="raw">{{ e.raw }}</pre>
            }
          </div>
        }
      }

      <h3>Steps</h3>
      <table>
        <thead>
          <tr>
            <th>Step</th>
            <th>State</th>
            <th>Started</th>
            <th>Finished</th>
            <th>Usage</th>
            <th>Memory</th>
            <th>Error</th>
          </tr>
        </thead>
        <tbody>
          @for (s of steps(); track s.id) {
            <tr>
              <td>{{ s.step_id }}</td>
              <td><span class="badge" [class]="'badge ' + s.state">{{ s.state }}</span></td>
              <td>{{ s.started_at ? (s.started_at | date: 'short') : '—' }}</td>
              <td>{{ s.completed_at ? (s.completed_at | date: 'short') : '—' }}</td>
              <td>
                @let u = usage(s);
                @if (u) {
                  <code
                    class="usage"
                    [class]="'usage ' + costSkew(u)"
                    [title]="usageTooltip(u)"
                  >{{ formatUsage(u) }}</code>
                } @else {
                  <span class="muted">—</span>
                }
              </td>
              <td>
                @let mh = memoryHash(s);
                @if (mh) {
                  <code class="mh" [title]="mh">{{ shortHash(mh) }}</code>
                } @else {
                  <span class="muted">—</span>
                }
              </td>
              <td class="error">{{ s.error ?? '' }}</td>
            </tr>
          }
        </tbody>
      </table>

      <h3>Audit log</h3>
      @if (auditEntries().length === 0) {
        <p class="muted">(none — needs Admin or Auditor role)</p>
      } @else {
        <ul class="audit">
          @for (e of auditEntries(); track e.id) {
            <li>
              <span class="when">{{ e.timestamp | date: 'shortTime' }}</span>
              <span class="action">{{ e.action }}</span>
              @if (e.step_id) {
                <span class="step">({{ e.step_id }})</span>
              }
            </li>
          }
        </ul>
      }
    }
  `,
  styles: [
    `
      .meta {
        background: var(--panel);
        border: 1px solid var(--border);
        border-radius: 4px;
        padding: 12px 16px;
        margin: 12px 0;
        display: grid;
        gap: 4px;
      }
      .actions {
        display: flex;
        gap: 8px;
        margin: 12px 0;
      }
      .error {
        color: var(--err);
      }
      .muted {
        color: var(--muted);
      }
      ul.audit {
        list-style: none;
        padding: 0;
        background: var(--panel);
        border: 1px solid var(--border);
        border-radius: 4px;
      }
      ul.audit li {
        padding: 6px 12px;
        border-bottom: 1px solid var(--border);
        display: flex;
        gap: 12px;
        font-size: 13px;
      }
      ul.audit li:last-child {
        border-bottom: 0;
      }
      .when {
        color: var(--muted);
        font-variant-numeric: tabular-nums;
      }
      .action {
        font-weight: 500;
      }
      .step {
        color: var(--muted);
      }
      h2 {
        display: flex;
        align-items: center;
        gap: 12px;
      }
      .eval {
        background: var(--panel);
        border: 1px solid var(--border);
        border-radius: 4px;
        padding: 12px 16px;
        margin: 8px 0;
      }
      .eval-head {
        display: flex;
        align-items: center;
        gap: 12px;
        margin-bottom: 8px;
      }
      .eval-head .step {
        color: var(--muted);
        font-family: ui-monospace, monospace;
        font-size: 13px;
      }
      .scores {
        display: flex;
        gap: 16px;
        flex-wrap: wrap;
      }
      .score {
        display: flex;
        flex-direction: column;
        gap: 2px;
      }
      .score .label {
        color: var(--muted);
        font-size: 12px;
      }
      .score .value {
        font-weight: 600;
        font-size: 16px;
        font-variant-numeric: tabular-nums;
      }
      .score .value.good {
        color: var(--ok);
      }
      .score .value.warn {
        color: var(--warn);
      }
      .score .value.err {
        color: var(--err);
      }
      .reasoning {
        margin-top: 8px;
        font-size: 14px;
        color: var(--text);
      }
      ul.issues {
        margin: 6px 0 0;
        padding-left: 20px;
        color: var(--err);
        font-size: 13px;
      }
      pre.raw {
        background: var(--bg);
        border: 1px solid var(--border);
        border-radius: 4px;
        padding: 8px;
        font-size: 12px;
        max-height: 160px;
        overflow: auto;
      }
      code.mh {
        font-family: ui-monospace, monospace;
        font-size: 12px;
        color: var(--muted);
      }
      code.usage {
        font-family: ui-monospace, monospace;
        font-size: 12px;
        color: var(--muted);
        white-space: nowrap;
      }
      code.usage.output-heavy {
        color: var(--warn);
        font-weight: 500;
      }
    `,
  ],
})
export class InstanceDetailComponent implements OnInit, OnDestroy {
  @Input({ required: true }) id!: string;

  private readonly api = inject(ApiService);
  private readonly events = inject(EventsService);
  private readonly router = inject(Router);
  private readonly destroy$ = new Subject<void>();

  readonly instance = signal<WorkflowInstance | null>(null);
  readonly steps = signal<StepExecution[]>([]);
  readonly auditEntries = signal<AuditEntry[]>([]);
  readonly loading = signal(true);
  readonly error = signal<string | null>(null);

  readonly evaluations = computed<EvaluationResult[]>(() => extractEvaluations(this.steps()));

  readonly scoreClass = scoreClass;

  ngOnInit(): void {
    this.refresh();
    // Polling refresh — catches state transitions the WebSocket doesn't emit
    // (the `events` bus only mirrors audit appends, not instance state).
    interval(3000)
      .pipe(takeUntil(this.destroy$))
      .subscribe(() => this.refresh());
    // Live audit-event stream — appended to `auditEntries` immediately on
    // arrival. Duplicate IDs (also picked up by polling) are filtered out.
    this.events
      .stream()
      .pipe(takeUntil(this.destroy$))
      .subscribe((entry) => {
        if (entry.workflow_instance_id !== this.id) return;
        this.auditEntries.update((current) => {
          if (current.some((e) => e.id === entry.id)) return current;
          return [...current, entry];
        });
      });
  }

  ngOnDestroy(): void {
    this.destroy$.next();
    this.destroy$.complete();
  }

  short(id: string): string {
    return id.slice(0, 8);
  }

  memoryHash(s: StepExecution): string | null {
    const value = s.output?.['memory_hash'];
    return typeof value === 'string' ? value : null;
  }

  shortHash(hash: string): string {
    // Strip "sha256:" prefix and show first 8 chars.
    const stripped = hash.startsWith('sha256:') ? hash.slice(7) : hash;
    return stripped.slice(0, 8);
  }

  usage(s: StepExecution): TokenUsage | null {
    return extractUsage(s);
  }

  readonly formatUsage = formatUsage;
  readonly usageTooltip = usageTooltip;
  readonly costSkew = costSkew;

  action(name: 'pause' | 'resume' | 'retry' | 'kill'): void {
    const op =
      name === 'pause'
        ? this.api.pauseInstance(this.id)
        : name === 'resume'
        ? this.api.resumeInstance(this.id)
        : name === 'retry'
        ? this.api.retryInstance(this.id)
        : this.api.killInstance(this.id);
    op.subscribe({
      next: () => this.refresh(),
      error: (err) => this.error.set(err.error?.detail ?? err.message ?? 'Action failed'),
    });
  }

  private refresh(): void {
    this.api.getInstance(this.id).subscribe({
      next: (data: InstanceDetail) => {
        this.instance.set(data.instance);
        this.steps.set(data.steps);
        this.loading.set(false);
        this.error.set(null);
      },
      error: (err) => {
        this.error.set(err.error?.detail ?? err.message ?? 'Failed to load instance');
        this.loading.set(false);
      },
    });

    this.api.instanceAudit(this.id).subscribe({
      next: (entries) => this.auditEntries.set(entries),
      error: () => {
        // Auditor/Admin role required; ignore silently for non-privileged users.
        this.auditEntries.set([]);
      },
    });
  }
}
