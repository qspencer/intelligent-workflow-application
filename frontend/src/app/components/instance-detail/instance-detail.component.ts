import { CommonModule, DatePipe } from '@angular/common';
import { Component, inject, Input, OnDestroy, OnInit, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { Subject, interval, takeUntil } from 'rxjs';

import { ApiService } from '../../services/api.service';
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

      <h3>Steps</h3>
      <table>
        <thead>
          <tr>
            <th>Step</th>
            <th>State</th>
            <th>Started</th>
            <th>Finished</th>
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
    `,
  ],
})
export class InstanceDetailComponent implements OnInit, OnDestroy {
  @Input({ required: true }) id!: string;

  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  private readonly destroy$ = new Subject<void>();

  readonly instance = signal<WorkflowInstance | null>(null);
  readonly steps = signal<StepExecution[]>([]);
  readonly auditEntries = signal<AuditEntry[]>([]);
  readonly loading = signal(true);
  readonly error = signal<string | null>(null);

  ngOnInit(): void {
    this.refresh();
    interval(3000)
      .pipe(takeUntil(this.destroy$))
      .subscribe(() => this.refresh());
  }

  ngOnDestroy(): void {
    this.destroy$.next();
    this.destroy$.complete();
  }

  short(id: string): string {
    return id.slice(0, 8);
  }

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
