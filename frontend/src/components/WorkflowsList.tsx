import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import { api, errorMessage } from '../api/client';
import { hasRole } from '../lib/auth';
import type { WorkflowDefinition } from '../types';

interface BatchProgress {
  done: number;
  errors: number;
  total: number;
}

/** Strip markdown noise from a description so it reads cleanly in a cell. */
function describe(raw: string | undefined): string {
  if (!raw) return '—';
  const cleaned = raw
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/\*([^*]+)\*/g, '$1')
    .replace(/^#+\s*/gm, '')
    .replace(/\s+/g, ' ')
    .trim();
  return cleaned.length > 120 ? cleaned.slice(0, 117) + '…' : cleaned;
}

export function WorkflowsList() {
  const navigate = useNavigate();

  const [definitions, setDefinitions] = useState<WorkflowDefinition[]>([]);
  const [instanceCounts, setInstanceCounts] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Import dialog
  const [importOpen, setImportOpen] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [importText, setImportText] = useState('');
  const [importFormat, setImportFormat] = useState<'yaml' | 'json'>('yaml');

  // Run dialog
  const [runOpen, setRunOpen] = useState<WorkflowDefinition | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [runSubmitting, setRunSubmitting] = useState(false);
  const [runPayloadText, setRunPayloadText] = useState('{}');
  const [runBatchMode, setRunBatchMode] = useState(false);
  const [runProgress, setRunProgress] = useState<BatchProgress | null>(null);
  const runOpenRef = useRef<WorkflowDefinition | null>(null);
  runOpenRef.current = runOpen;

  const refresh = useCallback(async (): Promise<void> => {
    setLoading(true);
    try {
      const data = await api.listWorkflows();
      setDefinitions(data);
    } catch (err) {
      setError(errorMessage(err, 'Failed to load workflows'));
    } finally {
      setLoading(false);
    }
    // Counts are best-effort; failure just shows 0.
    try {
      setInstanceCounts(await api.workflowInstanceCounts());
    } catch {
      setInstanceCounts({});
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // ---- Import ----
  function openImport(): void {
    setImportText('');
    setImportFormat('yaml');
    setImportError(null);
    setImportOpen(true);
  }
  function closeImport(): void {
    if (submitting) return;
    setImportOpen(false);
  }
  async function submitImport(): Promise<void> {
    const body = importText.trim();
    if (!body) return;
    setSubmitting(true);
    setImportError(null);
    try {
      await api.importWorkflow(body, importFormat);
      setSubmitting(false);
      setImportOpen(false);
      void refresh();
    } catch (err) {
      setSubmitting(false);
      setImportError(errorMessage(err, 'Import failed'));
    }
  }

  // ---- Run ----
  function openRun(wf: WorkflowDefinition): void {
    const example = wf.trigger?.example_payload;
    setRunPayloadText(
      example && Object.keys(example).length > 0
        ? JSON.stringify(example, null, 2)
        : '{}',
    );
    setRunBatchMode(false);
    setRunError(null);
    setRunProgress(null);
    setRunOpen(wf);
  }
  function closeRun(): void {
    if (runSubmitting) return;
    setRunOpen(null);
    setRunProgress(null);
  }

  /** Toggle batch mode: wrap/unwrap the current payload to match the shape. */
  function onBatchModeChange(batch: boolean, wf: WorkflowDefinition): void {
    setRunBatchMode(batch);
    const trimmed = runPayloadText.trim();
    try {
      const parsed = trimmed ? JSON.parse(trimmed) : null;
      if (batch && parsed && !Array.isArray(parsed) && typeof parsed === 'object') {
        setRunPayloadText(JSON.stringify([parsed], null, 2));
      } else if (!batch && Array.isArray(parsed) && parsed.length > 0) {
        setRunPayloadText(JSON.stringify(parsed[0], null, 2));
      } else if (batch && !parsed) {
        const example = wf.trigger?.example_payload;
        setRunPayloadText(
          example && Object.keys(example).length > 0
            ? JSON.stringify([example], null, 2)
            : '[]',
        );
      }
    } catch {
      // Mid-edit invalid JSON — leave it alone.
    }
    setRunError(null);
    setRunProgress(null);
  }

  function submitRun(wf: WorkflowDefinition): void {
    const text = runPayloadText.trim() || (runBatchMode ? '[]' : '{}');
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch (e) {
      setRunError(`Invalid JSON: ${(e as Error).message}`);
      return;
    }
    if (runBatchMode) void fireBatch(wf, parsed);
    else void fireSingle(wf, parsed);
  }

  async function fireSingle(wf: WorkflowDefinition, parsed: unknown): Promise<void> {
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      setRunError('Trigger payload must be a JSON object.');
      return;
    }
    setRunSubmitting(true);
    setRunError(null);
    try {
      const res = await api.runWorkflow(wf.id, parsed as Record<string, unknown>);
      setRunSubmitting(false);
      setRunOpen(null);
      navigate(`/instances/${res.instance_id}`);
    } catch (err) {
      setRunSubmitting(false);
      setRunError(errorMessage(err, 'Run failed'));
    }
  }

  /** Fire one instance per array element, sequentially. Continues on errors;
   *  the progress counter tracks done + errors so the dialog shows a running
   *  tally instead of blanking on first failure. */
  async function fireBatch(wf: WorkflowDefinition, parsed: unknown): Promise<void> {
    if (!Array.isArray(parsed)) {
      setRunError('Batch mode requires a JSON array.');
      return;
    }
    if (parsed.length === 0) {
      setRunError('Batch array is empty.');
      return;
    }
    const bad = parsed.findIndex((p) => !p || typeof p !== 'object' || Array.isArray(p));
    if (bad >= 0) {
      setRunError(`Element ${bad} is not a JSON object.`);
      return;
    }

    const total = parsed.length;
    setRunSubmitting(true);
    setRunError(null);
    setRunProgress({ done: 0, errors: 0, total });

    let done = 0;
    let errors = 0;
    for (const payload of parsed as Record<string, unknown>[]) {
      try {
        await api.runWorkflow(wf.id, payload);
      } catch {
        errors += 1;
      }
      done += 1;
      setRunProgress({ done, errors, total });
    }
    setRunSubmitting(false);
    if (errors === 0) {
      // Let the user see "Fired N of N" before navigating away.
      setTimeout(() => {
        if (runOpenRef.current) {
          setRunOpen(null);
          setRunProgress(null);
          navigate(`/instances?workflow_id=${encodeURIComponent(wf.id)}`);
        }
      }, 800);
    }
  }

  const batchDone =
    runProgress !== null && runProgress.done === runProgress.total;

  return (
    <div className="page-workflows">
      <div className="header">
        <h2>Workflows</h2>
        <button onClick={openImport}>Import workflow</button>
      </div>

      {loading ? (
        <p>Loading…</p>
      ) : error ? (
        <p className="error">{error}</p>
      ) : definitions.length === 0 ? (
        <p>No workflows registered yet.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Description</th>
              <th className="num-col">Instances</th>
              <th className="actions-col">Actions</th>
            </tr>
          </thead>
          <tbody>
            {definitions.map((wf) => (
              <tr key={wf.id}>
                <td>
                  <Link className="name-cell" to={`/canvas/${wf.id}`} title="Open the workflow canvas">
                    {wf.name}
                  </Link>
                  <code className="muted">{wf.id}</code>
                </td>
                <td>
                  <span title={wf.description || ''}>{describe(wf.description)}</span>
                </td>
                <td className="num-col">
                  <Link
                    to={`/instances?workflow_id=${encodeURIComponent(wf.id)}`}
                    title={`View ${instanceCounts[wf.id] || 0} instance(s) of this workflow`}
                  >
                    {instanceCounts[wf.id] || 0}
                  </Link>
                </td>
                <td className="actions-col">
                  <button onClick={() => openRun(wf)}>Run</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {runOpen && (
        <div className="dialog-overlay" onClick={closeRun}>
          <div className="dialog large" onClick={(e) => e.stopPropagation()}>
            <h3>
              Run <code>{runOpen.id}</code>
            </h3>
            <p className="muted">
              {runBatchMode
                ? 'JSON array — fires one workflow instance per array element.'
                : 'JSON object passed verbatim as the trigger payload.'}
              {!hasRole(['admins', 'operators']) && (
                <span> Operator or Admin role required.</span>
              )}
            </p>
            <label className="mode-toggle">
              <input
                type="checkbox"
                checked={runBatchMode}
                onChange={(e) => onBatchModeChange(e.target.checked, runOpen)}
                disabled={runSubmitting}
              />
              Batch mode (paste a JSON array; one instance per element)
            </label>
            <textarea
              rows={20}
              placeholder={runBatchMode ? '[{...}, {...}]' : '{"file_path": "/abs/path/to/some.pdf"}'}
              value={runPayloadText}
              onChange={(e) => setRunPayloadText(e.target.value)}
              disabled={runSubmitting}
            />
            {runProgress && (
              <p className="muted">
                {runProgress.done < runProgress.total ? (
                  <>
                    Firing {runProgress.done + 1} of {runProgress.total}…
                    {runProgress.errors > 0 && (
                      <span className="error">
                        {' '}
                        ({runProgress.errors} error
                        {runProgress.errors === 1 ? '' : 's'} so far)
                      </span>
                    )}
                  </>
                ) : (
                  <>
                    Fired {runProgress.done - runProgress.errors} of {runProgress.total}.
                    {runProgress.errors > 0 && (
                      <span className="error"> {runProgress.errors} failed.</span>
                    )}
                  </>
                )}
              </p>
            )}
            {runError && <p className="error">{runError}</p>}
            <div className="dialog-actions">
              <button onClick={closeRun} disabled={runSubmitting}>
                {batchDone ? 'Close' : 'Cancel'}
              </button>
              <button
                className="primary"
                onClick={() => submitRun(runOpen)}
                disabled={runSubmitting || batchDone}
              >
                {runSubmitting
                  ? runBatchMode
                    ? 'Firing…'
                    : 'Running…'
                  : runBatchMode
                    ? 'Fire batch'
                    : 'Run'}
              </button>
            </div>
          </div>
        </div>
      )}

      {importOpen && (
        <div className="dialog-overlay" onClick={closeImport}>
          <div className="dialog large" onClick={(e) => e.stopPropagation()}>
            <h3>Import workflow</h3>
            <p className="muted">
              Paste a YAML or JSON workflow definition.
              {!hasRole(['admins', 'designers']) && (
                <span> Designer or Admin role required.</span>
              )}
            </p>
            <textarea
              rows={20}
              placeholder={'id: my-workflow\nname: ...'}
              value={importText}
              onChange={(e) => setImportText(e.target.value)}
              disabled={submitting}
            />
            <label className="format">
              Format:
              <select
                value={importFormat}
                onChange={(e) => setImportFormat(e.target.value as 'yaml' | 'json')}
                disabled={submitting}
              >
                <option value="yaml">YAML</option>
                <option value="json">JSON</option>
              </select>
            </label>
            {importError && <p className="error">{importError}</p>}
            <div className="dialog-actions">
              <button onClick={closeImport} disabled={submitting}>
                Cancel
              </button>
              <button
                className="primary"
                onClick={() => void submitImport()}
                disabled={submitting || !importText.trim()}
              >
                {submitting ? 'Importing…' : 'Import'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
