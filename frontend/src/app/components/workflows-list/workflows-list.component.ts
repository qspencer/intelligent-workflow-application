import { CommonModule } from '@angular/common';
import { Component, inject, OnInit, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { ApiService } from '../../services/api.service';
import { WorkflowDefinition } from '../../types';

@Component({
  selector: 'wp-workflows-list',
  standalone: true,
  imports: [CommonModule, RouterLink],
  template: `
    <h2>Workflows</h2>
    @if (loading()) {
      <p>Loading…</p>
    } @else if (error()) {
      <p class="error">{{ error() }}</p>
    } @else if (definitions().length === 0) {
      <p>No workflows registered yet.</p>
    } @else {
      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>Name</th>
            <th>Description</th>
            <th>Instances</th>
          </tr>
        </thead>
        <tbody>
          @for (wf of definitions(); track wf.id) {
            <tr>
              <td><code>{{ wf.id }}</code></td>
              <td>{{ wf.name }}</td>
              <td>{{ wf.description || '—' }}</td>
              <td>
                <a [routerLink]="['/instances']" [queryParams]="{ workflow_id: wf.id }">
                  View
                </a>
              </td>
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
        color: var(--muted);
      }
    `,
  ],
})
export class WorkflowsListComponent implements OnInit {
  private readonly api = inject(ApiService);

  readonly definitions = signal<WorkflowDefinition[]>([]);
  readonly loading = signal(true);
  readonly error = signal<string | null>(null);

  ngOnInit(): void {
    this.api.listWorkflows().subscribe({
      next: (data) => {
        this.definitions.set(data);
        this.loading.set(false);
      },
      error: (err) => {
        this.error.set(err.message ?? 'Failed to load workflows');
        this.loading.set(false);
      },
    });
  }
}
