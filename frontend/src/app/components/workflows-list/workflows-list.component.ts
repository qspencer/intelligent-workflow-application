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
            <th>Name</th>
            <th>Description</th>
            <th class="num-col">Instances</th>
            <th class="actions-col">Actions</th>
          </tr>
        </thead>
        <tbody>
          @for (wf of definitions(); track wf.id) {
            <tr>
              <td>
                <div class="name-cell">{{ wf.name }}</div>
                <code class="muted">{{ wf.id }}</code>
              </td>
              <td>
                <span [title]="wf.description || ''">
                  {{ describe(wf.description) }}
                </span>
              </td>
              <td class="num-col">
                <a [routerLink]="['/instances']" [queryParams]="{ workflow_id: wf.id }"
                   title="View {{ instanceCounts()[wf.id] || 0 }} instance(s) of this workflow">
                  {{ instanceCounts()[wf.id] || 0 }}
                </a>
              </td>
              <td class="actions-col">
                <button (click)="openRun(wf)">Run</button>
              </td>
            </tr>
          }
        </tbody>
      </table>
    }

    @if (runOpen(); as wf) {
      <div class="dialog-overlay" (click)="closeRun()">
        <div class="dialog large" (click)="$event.stopPropagation()">
          <h3>Run <code>{{ wf.id }}</code></h3>
          <p class="muted">
            @if (runBatchMode) {
              JSON <strong>array</strong> — fires one workflow instance per array element.
            } @else {
              JSON object passed verbatim as the trigger payload.
            }
            @if (!hasRole(['admins', 'operators'])) {
              <span> Operator or Admin role required.</span>
            }
          </p>
          <label class="mode-toggle">
            <input
              type="checkbox"
              [(ngModel)]="runBatchMode"
              (ngModelChange)="onBatchModeChange($event, wf)"
              [disabled]="runSubmitting()"
            />
            Batch mode (paste a JSON array; one instance per element)
          </label>
          <textarea
            rows="20"
            [placeholder]="runBatchMode ? '[{...}, {...}]' : '{\&quot;file_path\&quot;: \&quot;/abs/path/to/some.pdf\&quot;}'"
            [(ngModel)]="runPayloadText"
            [disabled]="runSubmitting()"
          ></textarea>
          @if (runProgress(); as p) {
            <p class="muted">
              @if (p.done < p.total) {
                Firing {{ p.done + 1 }} of {{ p.total }}…
                @if (p.errors > 0) {
                  <span class="error">({{ p.errors }} error{{ p.errors === 1 ? '' : 's' }} so far)</span>
                }
              } @else {
                Fired {{ p.done - p.errors }} of {{ p.total }}.
                @if (p.errors > 0) {
                  <span class="error">{{ p.errors }} failed.</span>
                }
              }
            </p>
          }
          @if (runError()) {
            <p class="error">{{ runError() }}</p>
          }
          <div class="dialog-actions">
            <button (click)="closeRun()" [disabled]="runSubmitting()">
              {{ runProgress() && runProgress()!.done === runProgress()!.total ? 'Close' : 'Cancel' }}
            </button>
            <button
              class="primary"
              (click)="submitRun(wf)"
              [disabled]="runSubmitting() || (runProgress() !== null && runProgress()!.done === runProgress()!.total)"
            >
              @if (runSubmitting()) {
                {{ runBatchMode ? 'Firing…' : 'Running…' }}
              } @else {
                {{ runBatchMode ? 'Fire batch' : 'Run' }}
              }
            </button>
          </div>
        </div>
      </div>
    }

    @if (importOpen()) {
      <div class="dialog-overlay" (click)="closeImport()">
        <div class="dialog large" (click)="$event.stopPropagation()">
          <h3>Import workflow</h3>
          <p class="muted">
            Paste a YAML or JSON workflow definition.
            @if (!hasRole(['admins', 'designers'])) {
              <span> Designer or Admin role required.</span>
            }
          </p>
          <textarea
            rows="20"
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
      /* The Run + Import dialogs hold JSON/YAML payloads up to ~50
         lines (e.g. a real GitHub PR body or an EmailMessage). The
         default 600px doesn't fit; large-variant takes the page width
         up to 1000px and pushes the textarea to fill. */
      .dialog.large {
        width: 1000px;
      }
      .dialog.large textarea {
        min-height: 400px;
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
      .format,
      .mode-toggle {
        display: flex;
        align-items: center;
        gap: 8px;
        font-size: 13px;
        color: var(--muted);
      }
      .mode-toggle input[type='checkbox'] {
        margin: 0;
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
      /* Stack workflow name + ID in one cell so the ID isn't burning a
         whole column for an identifier the operator rarely needs. */
      .name-cell {
        font-weight: 500;
      }
      /* Right-align numeric and action columns; matches admin-table
         conventions. */
      .num-col {
        text-align: right;
        width: 90px;
        font-variant-numeric: tabular-nums;
      }
      .actions-col {
        width: 100px;
      }
    `,
  ],
})
export class WorkflowsListComponent implements OnInit {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);

  readonly definitions = signal<WorkflowDefinition[]>([]);
  readonly instanceCounts = signal<Record<string, number>>({});
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
  /** ngModel target for the "Batch mode" toggle. Plain field — checkbox
   *  binding doesn't need a signal. */
  runBatchMode = false;
  /** Tracks progress during a batch fire: `{done, errors, total}`. Null
   *  when no batch is in flight or completed. Rendered as
   *  "Firing 3 of 50…" / "Fired 47 of 50. 3 failed." */
  readonly runProgress = signal<{
    done: number;
    errors: number;
    total: number;
  } | null>(null);

  ngOnInit(): void {
    this.refresh();
  }

  /** Whether the current dev-mode identity has any of the given group
   *  values. Mirrors what the auth interceptor sends in `X-Dev-Groups`
   *  (which defaults to 'admins' when localStorage is unset). Used to
   *  suppress redundant "Role required" hints in dialogs the user can
   *  already submit. In production OIDC mode there's no localStorage
   *  to read; this returns false there, so the hint stays visible. */
  hasRole(allowed: string[]): boolean {
    const stored = localStorage.getItem('wp.groups');
    // Match the interceptor's default: an unset localStorage acts as
    // 'admins' in dev mode.
    const effective = stored ?? 'admins';
    return allowed.includes(effective);
  }

  /** Strip the markdown noise (code-fences, asterisk emphasis, headers,
   *  newlines) from a workflow description so it reads cleanly in a
   *  table cell. The full original text is shown on hover via title=
   *  on the surrounding span. */
  describe(raw: string | undefined): string {
    if (!raw) return '—';
    const cleaned = raw
      .replace(/`([^`]+)`/g, '$1') // `code` → code
      .replace(/\*\*([^*]+)\*\*/g, '$1') // **bold** → bold
      .replace(/\*([^*]+)\*/g, '$1') // *em* → em
      .replace(/^#+\s*/gm, '') // strip heading markers
      .replace(/\s+/g, ' ') // collapse whitespace + newlines
      .trim();
    return cleaned.length > 120 ? cleaned.slice(0, 117) + '…' : cleaned;
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
    // Fire in parallel with the definitions fetch — failure here only
    // means counts show as 0, which is fine.
    this.api.workflowInstanceCounts().subscribe({
      next: (counts) => this.instanceCounts.set(counts),
      error: () => this.instanceCounts.set({}),
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
    // Pre-fill with the workflow's declared example_payload if present;
    // otherwise leave the box at "{}". JSON.stringify with 2-space indent
    // gives operators something readable that they can edit in place.
    const example = wf.trigger?.example_payload;
    this.runPayloadText =
      example && Object.keys(example).length > 0
        ? JSON.stringify(example, null, 2)
        : '{}';
    this.runBatchMode = false;
    this.runError.set(null);
    this.runProgress.set(null);
    this.runOpen.set(wf);
  }

  closeRun(): void {
    if (this.runSubmitting()) return;
    this.runOpen.set(null);
    this.runProgress.set(null);
  }

  /** When the user toggles batch mode, re-shape the textarea: turning
   *  it ON wraps the current single-object payload in `[ ... ]` so the
   *  user has a starting point; turning it OFF unwraps the first
   *  element if the textarea currently holds an array. Either way the
   *  user can edit freely. */
  onBatchModeChange(batch: boolean, wf: WorkflowDefinition): void {
    const trimmed = this.runPayloadText.trim();
    try {
      const parsed = trimmed ? JSON.parse(trimmed) : null;
      if (batch && parsed && !Array.isArray(parsed) && typeof parsed === 'object') {
        // single object → wrap as a one-element array
        this.runPayloadText = JSON.stringify([parsed], null, 2);
      } else if (!batch && Array.isArray(parsed) && parsed.length > 0) {
        // array → unwrap first element
        this.runPayloadText = JSON.stringify(parsed[0], null, 2);
      } else if (batch && !parsed) {
        // empty → seed with [example_payload] if available
        const example = wf.trigger?.example_payload;
        this.runPayloadText =
          example && Object.keys(example).length > 0
            ? JSON.stringify([example], null, 2)
            : '[]';
      }
    } catch {
      // If the current text doesn't parse, leave it alone — the user
      // is mid-edit and we'd be hostile to clobber.
    }
    this.runError.set(null);
    this.runProgress.set(null);
  }

  submitRun(wf: WorkflowDefinition): void {
    const text = this.runPayloadText.trim() || (this.runBatchMode ? '[]' : '{}');
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch (e) {
      this.runError.set(`Invalid JSON: ${(e as Error).message}`);
      return;
    }

    if (this.runBatchMode) {
      this.fireBatch(wf, parsed);
    } else {
      this.fireSingle(wf, parsed);
    }
  }

  private fireSingle(wf: WorkflowDefinition, parsed: unknown): void {
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      this.runError.set('Trigger payload must be a JSON object.');
      return;
    }
    const payload = parsed as Record<string, unknown>;
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

  /** Fire one workflow instance per element of `parsed`, sequentially.
   *  Sequential because:
   *    - The frontend can't easily throttle on the user's side; the
   *      backend's gmail/httplib2 thread safety was an issue we
   *      already had to lock around — keep dispatch simple here.
   *    - Per-instance latency is dominated by Bedrock anyway (3-8s
   *      per agent call). At 50 papers, ~3 min total. Acceptable.
   *  Continues on errors; the progress signal tracks done + errors so
   *  the dialog shows a running count instead of blanking on first
   *  failure. */
  private async fireBatch(wf: WorkflowDefinition, parsed: unknown): Promise<void> {
    if (!Array.isArray(parsed)) {
      this.runError.set('Batch mode requires a JSON array.');
      return;
    }
    if (parsed.length === 0) {
      this.runError.set('Batch array is empty.');
      return;
    }
    const bad = parsed.findIndex((p) => !p || typeof p !== 'object' || Array.isArray(p));
    if (bad >= 0) {
      this.runError.set(`Element ${bad} is not a JSON object.`);
      return;
    }

    const total = parsed.length;
    this.runSubmitting.set(true);
    this.runError.set(null);
    this.runProgress.set({ done: 0, errors: 0, total });

    let done = 0;
    let errors = 0;
    for (const payload of parsed as Record<string, unknown>[]) {
      try {
        await new Promise<void>((resolve, reject) => {
          this.api.runWorkflow(wf.id, payload).subscribe({
            next: () => resolve(),
            error: (err) => reject(err),
          });
        });
      } catch {
        errors += 1;
      }
      done += 1;
      this.runProgress.set({ done, errors, total });
    }
    this.runSubmitting.set(false);
    // Don't auto-close: the user wants to see the final tally. They
    // can click Close (which now reads "Close" rather than "Cancel"
    // once done === total) to dismiss. Optimistically navigate them
    // toward the instances list if there were no errors.
    if (errors === 0) {
      // Wait a beat so the user can see "Fired 50 of 50" before nav.
      setTimeout(() => {
        if (this.runOpen()) {
          this.runOpen.set(null);
          this.runProgress.set(null);
          this.router.navigate(['/instances'], { queryParams: { workflow_id: wf.id } });
        }
      }, 800);
    }
  }
}
