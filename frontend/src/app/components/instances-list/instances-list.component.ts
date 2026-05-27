import { CommonModule, DatePipe } from '@angular/common';
import { Component, inject, OnDestroy, OnInit, signal } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { Subject, interval, takeUntil } from 'rxjs';

import { ApiService } from '../../services/api.service';
import { WorkflowInstance } from '../../types';

@Component({
  selector: 'wp-instances-list',
  standalone: true,
  imports: [CommonModule, DatePipe, RouterLink],
  template: `
    <h2>Workflow Instances</h2>
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
            </tr>
          }
        </tbody>
      </table>
    }
  `,
  styles: [
    `
      .error {
        color: var(--err);
      }
      code {
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
