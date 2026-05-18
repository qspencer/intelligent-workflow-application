import { CommonModule } from '@angular/common';
import { Component, inject, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';

import { ApiService } from '../../services/api.service';
import { WorkflowDefinition } from '../../types';

@Component({
  selector: 'wp-workflows-list',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  template: `
    <div class="header">
      <h2>Workflows</h2>
      <button (click)="openImport()">Import workflow</button>
    </div>

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
                <button class="link" (click)="openRun(wf)">Run</button>
                <a [routerLink]="['/instances']" [queryParams]="{ workflow_id: wf.id }">
                  View
                </a>
              </td>
            </tr>
          }
        </tbody>
      </table>
    }

    @if (runOpen(); as wf) {
      <div class="dialog-overlay" (click)="closeRun()">
        <div class="dialog" (click)="$event.stopPropagation()">
          <h3>Run <code>{{ wf.id }}</code></h3>
          <p class="muted">
            JSON object passed verbatim as the trigger payload. Operator or Admin role required.
          </p>
          <textarea
            rows="8"
            placeholder='{"file_path": "/abs/path/to/some.pdf"}'
            [(ngModel)]="runPayloadText"
            [disabled]="runSubmitting()"
          ></textarea>
          @if (runError()) {
            <p class="error">{{ runError() }}</p>
          }
          <div class="dialog-actions">
            <button (click)="closeRun()" [disabled]="runSubmitting()">Cancel</button>
            <button
              class="primary"
              (click)="submitRun(wf)"
              [disabled]="runSubmitting()"
            >
              {{ runSubmitting() ? 'Running…' : 'Run' }}
            </button>
          </div>
        </div>
      </div>
    }

    @if (importOpen()) {
      <div class="dialog-overlay" (click)="closeImport()">
        <div class="dialog" (click)="$event.stopPropagation()">
          <h3>Import workflow</h3>
          <p class="muted">
            Paste a YAML or JSON workflow definition. Designer or Admin role required.
          </p>
          <textarea
            rows="14"
            placeholder="id: my-workflow&#10;name: ..."
            [(ngModel)]="importText"
            [disabled]="submitting()"
          ></textarea>
          <label class="format">
            Format:
            <select [(ngModel)]="importFormat" [disabled]="submitting()">
              <option value="yaml">YAML</option>
              <option value="json">JSON</option>
            </select>
          </label>
          @if (importError()) {
            <p class="error">{{ importError() }}</p>
          }
          <div class="dialog-actions">
            <button (click)="closeImport()" [disabled]="submitting()">Cancel</button>
            <button
              class="primary"
              (click)="submitImport()"
              [disabled]="submitting() || !importText.trim()"
            >
              {{ submitting() ? 'Importing…' : 'Import' }}
            </button>
          </div>
        </div>
      </div>
    }
  `,
  styles: [
    `
      .header {
        display: flex;
        align-items: center;
        justify-content: space-between;
      }
      .error {
        color: var(--err);
      }
      .muted {
        color: var(--muted);
        font-size: 13px;
      }
      code {
        font-size: 12px;
        color: var(--muted);
      }
      .dialog-overlay {
        position: fixed;
        inset: 0;
        background: rgba(0, 0, 0, 0.4);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 100;
      }
      .dialog {
        background: var(--panel);
        border-radius: 6px;
        padding: 20px 24px;
        width: 600px;
        max-width: 90vw;
        max-height: 90vh;
        overflow: auto;
        display: flex;
        flex-direction: column;
        gap: 12px;
      }
      .dialog h3 {
        margin: 0;
      }
      .dialog textarea {
        font-family: ui-monospace, monospace;
        font-size: 13px;
        padding: 8px;
        border: 1px solid var(--border);
        border-radius: 4px;
        resize: vertical;
      }
      .format {
        display: flex;
        align-items: center;
        gap: 8px;
        font-size: 13px;
        color: var(--muted);
      }
      .format select {
        padding: 2px 6px;
      }
      .dialog-actions {
        display: flex;
        justify-content: flex-end;
        gap: 8px;
      }
      .dialog-actions .primary {
        background: var(--accent);
        color: white;
        border: 0;
      }
      .dialog-actions .primary:disabled {
        opacity: 0.5;
      }
      button.link {
        background: none;
        border: 0;
        color: var(--accent);
        cursor: pointer;
        padding: 0;
        font: inherit;
        margin-right: 8px;
      }
      button.link:hover {
        text-decoration: underline;
      }
    `,
  ],
})
export class WorkflowsListComponent implements OnInit {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);

  readonly definitions = signal<WorkflowDefinition[]>([]);
  readonly loading = signal(true);
  readonly error = signal<string | null>(null);

  readonly importOpen = signal(false);
  readonly importError = signal<string | null>(null);
  readonly submitting = signal(false);
  importText = '';
  importFormat: 'yaml' | 'json' = 'yaml';

  readonly runOpen = signal<WorkflowDefinition | null>(null);
  readonly runError = signal<string | null>(null);
  readonly runSubmitting = signal(false);
  runPayloadText = '{}';

  ngOnInit(): void {
    this.refresh();
  }

  refresh(): void {
    this.loading.set(true);
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

  openImport(): void {
    this.importText = '';
    this.importFormat = 'yaml';
    this.importError.set(null);
    this.importOpen.set(true);
  }

  closeImport(): void {
    if (this.submitting()) return;
    this.importOpen.set(false);
  }

  submitImport(): void {
    const body = this.importText.trim();
    if (!body) return;
    this.submitting.set(true);
    this.importError.set(null);
    this.api.importWorkflow(body, this.importFormat).subscribe({
      next: () => {
        this.submitting.set(false);
        this.importOpen.set(false);
        this.refresh();
      },
      error: (err) => {
        this.submitting.set(false);
        const detail = err.error?.detail ?? err.message ?? 'Import failed';
        this.importError.set(typeof detail === 'string' ? detail : JSON.stringify(detail));
      },
    });
  }

  openRun(wf: WorkflowDefinition): void {
    this.runPayloadText = '{}';
    this.runError.set(null);
    this.runOpen.set(wf);
  }

  closeRun(): void {
    if (this.runSubmitting()) return;
    this.runOpen.set(null);
  }

  submitRun(wf: WorkflowDefinition): void {
    const text = this.runPayloadText.trim() || '{}';
    let payload: Record<string, unknown>;
    try {
      const parsed = JSON.parse(text);
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        this.runError.set('Trigger payload must be a JSON object.');
        return;
      }
      payload = parsed as Record<string, unknown>;
    } catch (e) {
      this.runError.set(`Invalid JSON: ${(e as Error).message}`);
      return;
    }

    this.runSubmitting.set(true);
    this.runError.set(null);
    this.api.runWorkflow(wf.id, payload).subscribe({
      next: (res) => {
        this.runSubmitting.set(false);
        this.runOpen.set(null);
        this.router.navigate(['/instances', res.instance_id]);
      },
      error: (err) => {
        this.runSubmitting.set(false);
        const detail = err.error?.detail ?? err.message ?? 'Run failed';
        this.runError.set(typeof detail === 'string' ? detail : JSON.stringify(detail));
      },
    });
  }
}
