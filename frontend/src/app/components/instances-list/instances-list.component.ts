import { CommonModule, DatePipe } from '@angular/common';
import { Component, computed, inject, OnDestroy, OnInit, signal } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { Subject, interval, takeUntil } from 'rxjs';

import { ApiService } from '../../services/api.service';
import { WorkflowInstance } from '../../types';

@Component({
  selector: 'wp-instances-list',
  standalone: true,
  imports: [CommonModule, DatePipe, RouterLink],
  template: `
    <div class="header-row">
      <h2>Workflow Instances</h2>
      <button
        class="danger"
        [disabled]="deletingAll() || terminalCount() === 0"
        (click)="deleteAllTerminal()"
        title="Delete every instance currently in Completed, Failed, or Killed status. Running, Pending, and Paused instances are not affected."
      >
        @if (deletingAll()) {
          Deleting…
        } @else {
          Delete Finished ({{ terminalCount() }})
        }
      </button>
    </div>
    @if (loading()) {
      <p>Loading…</p>
    } @else if (error()) {
      <p class="error">{{ error() }}</p>
    } @else if (instances().length === 0) {
      <p>No instances yet.</p>
    } @else {
      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>Workflow</th>
            <th>State</th>
            <th>Started</th>
            <th>Finished</th>
            <th class="actions-col"></th>
          </tr>
        </thead>
        <tbody>
          @for (inst of instances(); track inst.id) {
            <tr>
              <td>
                <a [routerLink]="['/instances', inst.id]"><code>{{ short(inst.id) }}</code></a>
              </td>
              <td>{{ inst.workflow_id }}</td>
              <td><span class="badge" [class]="'badge ' + inst.state">{{ inst.state }}</span></td>
              <td>{{ inst.started_at ? (inst.started_at | date: 'short') : '—' }}</td>
              <td>{{ inst.completed_at ? (inst.completed_at | date: 'short') : '—' }}</td>
              <td class="actions-col">
                @if (isTerminal(inst.state)) {
                  <button
                    class="danger small"
                    [disabled]="deleting() === inst.id"
                    (click)="deleteInstance(inst.id)"
                    title="Delete this {{ inst.state }} instance"
                  >Delete</button>
                }
              </td>
            </tr>
          }
        </tbody>
      </table>
    }
  `,
  styles: [
    `
      .header-row {
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-bottom: 12px;
      }
      .header-row h2 {
        margin: 0;
      }
      .error {
        color: var(--err);
      }
      code {
        font-size: 12px;
      }
      .actions-col {
        width: 90px;
        text-align: right;
      }
      button.small {
        padding: 2px 8px;
        font-size: 12px;
      }
    `,
  ],
})
export class InstancesListComponent implements OnInit, OnDestroy {
  private readonly api = inject(ApiService);
  private readonly route = inject(ActivatedRoute);
  private readonly destroy$ = new Subject<void>();

  readonly instances = signal<WorkflowInstance[]>([]);
  readonly loading = signal(true);
  readonly error = signal<string | null>(null);
  /** Instance ID currently being deleted (used to disable the row's
   *  button while the request is in flight). */
  readonly deleting = signal<string | null>(null);
  /** True while a bulk-delete request is in flight. Disables both the
   *  bulk button and individual row buttons to prevent overlapping
   *  state-mutating calls. */
  readonly deletingAll = signal(false);
  /** Number of currently-loaded instances eligible for bulk delete. */
  readonly terminalCount = computed(
    () => this.instances().filter((i) => this.isTerminal(i.state)).length,
  );

  ngOnInit(): void {
    this.refresh();
    interval(5000)
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

  /** Terminal states are the only ones eligible for delete. Backend
   *  enforces the same — UI just hides the affordance for live runs. */
  isTerminal(state: string): boolean {
    return state === 'completed' || state === 'failed' || state === 'killed';
  }

  deleteInstance(id: string): void {
    if (this.deleting()) return;
    if (!window.confirm(`Delete instance ${this.short(id)}? This cannot be undone.`)) return;
    this.deleting.set(id);
    this.api.deleteInstance(id).subscribe({
      next: () => {
        // Optimistically prune the local list so the row disappears
        // immediately; the next 5s poll would do this anyway.
        this.instances.update((rows) => rows.filter((r) => r.id !== id));
        this.deleting.set(null);
      },
      error: (err) => {
        this.error.set(err.message ?? 'Failed to delete instance');
        this.deleting.set(null);
      },
    });
  }

  deleteAllTerminal(): void {
    if (this.deletingAll()) return;
    const n = this.terminalCount();
    if (n === 0) return;
    if (
      !window.confirm(
        `Delete ${n} completed/failed/killed instance${n === 1 ? '' : 's'}? ` +
          `This cannot be undone. Running, pending, and paused instances are NOT affected.`,
      )
    ) {
      return;
    }
    this.deletingAll.set(true);
    this.api
      .deleteInstancesByStates(['completed', 'failed', 'killed'])
      .subscribe({
        next: () => {
          // Optimistically prune all terminal rows; the 5s poll will
          // reconcile any drift.
          this.instances.update((rows) => rows.filter((r) => !this.isTerminal(r.state)));
          this.deletingAll.set(false);
        },
        error: (err) => {
          this.error.set(err.message ?? 'Failed to bulk-delete instances');
          this.deletingAll.set(false);
        },
      });
  }

  private refresh(): void {
    const workflow_id = this.route.snapshot.queryParamMap.get('workflow_id') ?? undefined;
    this.api.listInstances({ workflow_id, limit: 50 }).subscribe({
      next: (data) => {
        this.instances.set(data);
        this.loading.set(false);
        this.error.set(null);
      },
      error: (err) => {
        this.error.set(err.message ?? 'Failed to load instances');
        this.loading.set(false);
      },
    });
  }
}
