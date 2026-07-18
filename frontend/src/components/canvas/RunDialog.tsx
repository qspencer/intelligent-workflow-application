import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import { api, errorMessage } from '../../api/client';
import { parseBatchInput } from '../../lib/batch';
import { hasRole } from '../../lib/auth';
import { removeAt, setIn } from '../../lib/run-form';
import type { BatchRunResult, CostEstimate, WorkflowDefinition } from '../../types';
import { FieldEditor, type Remove, type Update } from './FieldEditor';

/** Pre-run cost context (C6.2): average $/run from history, or model rates +
 *  budget when the workflow hasn't run yet. Best-effort — hidden on error. */
function CostEstimateLine({ est }: { est: CostEstimate }) {
  const aiSteps = est.models.length;
  return (
    <div className="run-estimate">
      {est.avg_cost_usd != null ? (
        <span>
          ≈ <strong>${est.avg_cost_usd.toFixed(4)}</strong>/run{' '}
          <span className="muted">
            (avg of {est.run_count} run{est.run_count === 1 ? '' : 's'})
          </span>
        </span>
      ) : (
        <span className="muted">
          No cost history yet — {aiSteps} AI step{aiSteps === 1 ? '' : 's'}
        </span>
      )}
      {est.max_total_tokens != null && (
        <span className="run-est-budget">
          budget {est.max_total_tokens.toLocaleString()} tok · {est.budget_action} at cap
        </span>
      )}
      {aiSteps > 0 && (
        <details className="run-est-models">
          <summary>model rates</summary>
          <ul>
            {est.models.map((m) => (
              <li key={m.step_id}>
                <code>{m.step_id}</code>: {m.model}
                {m.input_per_million != null
                  ? ` — $${m.input_per_million}/$${m.output_per_million} per 1M`
                  : ' — (unpriced)'}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

/** Live row-count / parse hint for the batch input (C8.1). */
function BatchCount({ text }: { text: string }) {
  try {
    const n = parseBatchInput(text).length;
    return (
      <p className="muted">
        {n} row{n === 1 ? '' : 's'} ready.
      </p>
    );
  } catch {
    return <p className="muted">Paste a JSON array of objects, or CSV with a header row.</p>;
  }
}

function initialPayload(def: WorkflowDefinition): Record<string, unknown> {
  const ex = def.trigger?.example_payload;
  return ex && Object.keys(ex).length > 0 ? structuredClone(ex) : {};
}

export function RunDialog({
  def,
  onClose,
  dryRun = false,
}: {
  def: WorkflowDefinition;
  onClose: () => void;
  dryRun?: boolean;
}) {
  const navigate = useNavigate();
  const seeded = initialPayload(def);
  const hasFields = Object.keys(seeded).length > 0;

  const [value, setValue] = useState<Record<string, unknown>>(seeded);
  // No example payload to build a form from → default to JSON entry.
  const [mode, setMode] = useState<'form' | 'json' | 'batch'>(hasFields ? 'form' : 'json');
  const [jsonText, setJsonText] = useState(JSON.stringify(seeded, null, 2));
  const [batchText, setBatchText] = useState('');
  const [batchResult, setBatchResult] = useState<BatchRunResult | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [estimate, setEstimate] = useState<CostEstimate | null>(null);

  useEffect(() => {
    let ignore = false;
    api
      .getCostEstimate(def.id)
      .then((e) => !ignore && setEstimate(e))
      .catch(() => !ignore && setEstimate(null));
    return () => {
      ignore = true;
    };
  }, [def.id]);

  const update: Update = (path, v) => setValue((prev) => setIn(prev, path, v) as Record<string, unknown>);
  const remove: Remove = (path) => setValue((prev) => removeAt(prev, path) as Record<string, unknown>);

  function switchMode(next: 'form' | 'json' | 'batch'): void {
    if (next === mode) return;
    if (next === 'json') {
      setJsonText(JSON.stringify(value, null, 2));
    } else if (next === 'form') {
      try {
        const parsed = JSON.parse(jsonText);
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
          setValue(parsed as Record<string, unknown>);
        }
      } catch {
        // Keep the form's last good value if the JSON is mid-edit.
      }
    }
    setError(null);
    setMode(next);
  }

  async function onBatchFile(file: File): Promise<void> {
    setBatchText(await file.text());
    setError(null);
  }

  async function submitBatch(): Promise<void> {
    let rows: Record<string, unknown>[];
    try {
      rows = parseBatchInput(batchText);
    } catch (e) {
      setError((e as Error).message);
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      setBatchResult(await api.runBatch(def.id, rows));
    } catch (err) {
      setError(errorMessage(err, 'Batch run failed'));
    } finally {
      setSubmitting(false);
    }
  }

  async function submit(): Promise<void> {
    let payload: unknown = value;
    if (mode === 'json') {
      try {
        payload = JSON.parse(jsonText);
      } catch (e) {
        setError(`Invalid JSON: ${(e as Error).message}`);
        return;
      }
    }
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
      setError('Trigger payload must be a JSON object.');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const res = dryRun
        ? await api.dryRunWorkflow(def.id, payload as Record<string, unknown>)
        : await api.runWorkflow(def.id, payload as Record<string, unknown>);
      onClose();
      // Flip the canvas into live (instance) mode — the C2 overlay takes over.
      navigate(`/canvas/${def.id}?instance=${res.instance_id}${dryRun ? '&dry=1' : ''}`);
    } catch (err) {
      setSubmitting(false);
      setError(errorMessage(err, dryRun ? 'Test failed' : 'Run failed'));
    }
  }

  return (
    <div className="dialog-overlay" onClick={() => !submitting && onClose()}>
      <div className="dialog large" onClick={(e) => e.stopPropagation()}>
        <h3>
          {dryRun ? 'Test' : 'Run'} <code>{def.id}</code>
        </h3>
        <p className="muted">
          Fill in the trigger details and {dryRun ? 'test' : 'run'}. The canvas will switch to
          a live view of the {dryRun ? 'test' : 'run'}.
          {!hasRole(['admins', 'org-admins', 'org-users']) && (
            <span> {dryRun ? 'Designer, Operator or Admin' : 'Operator or Admin'} role required.</span>
          )}
        </p>
        {dryRun && (
          <p className="run-sandbox">
            🧪 Sandbox — runs against a mock world with email/connector/browser tools disabled
            (live AI). Nothing real is touched.
          </p>
        )}

        {batchResult ? (
          <div className="batch-result">
            <p>
              Fired <strong>{batchResult.submitted}</strong> — {batchResult.succeeded} succeeded,{' '}
              {batchResult.failed} failed.
            </p>
            <ul className="batch-result-list">
              {batchResult.results.map((r) => (
                <li key={r.index}>
                  <span className="muted">row {r.index + 1}</span>{' '}
                  {r.ok && r.instance_id ? (
                    <Link to={`/canvas/${def.id}?instance=${r.instance_id}`} onClick={onClose}>
                      view run
                    </Link>
                  ) : (
                    <span className="error">{r.error ?? 'failed'}</span>
                  )}
                  {r.state && <span className="muted"> · {r.state}</span>}
                </li>
              ))}
            </ul>
            <div className="dialog-actions">
              <button className="primary" onClick={onClose}>
                Done
              </button>
            </div>
          </div>
        ) : (
          <>
            {estimate && <CostEstimateLine est={estimate} />}

            <div className="rf-mode">
              <button
                type="button"
                className={mode === 'form' ? 'active' : ''}
                disabled={!hasFields || submitting}
                onClick={() => switchMode('form')}
              >
                Form
              </button>
              <button
                type="button"
                className={mode === 'json' ? 'active' : ''}
                disabled={submitting}
                onClick={() => switchMode('json')}
              >
                Paste JSON
              </button>
              {!dryRun && (
                <button
                  type="button"
                  className={mode === 'batch' ? 'active' : ''}
                  disabled={submitting}
                  onClick={() => switchMode('batch')}
                >
                  Batch
                </button>
              )}
            </div>

            {mode === 'form' ? (
              hasFields ? (
                <div className="rf-form">
                  {Object.entries(value).map(([k, v]) => (
                    <FieldEditor
                      key={k}
                      label={k}
                      value={v}
                      path={[k]}
                      update={update}
                      remove={remove}
                    />
                  ))}
                </div>
              ) : (
                <p className="muted">
                  This workflow's trigger has no example payload — use “Paste JSON”.
                </p>
              )
            ) : mode === 'json' ? (
              <textarea
                className="rf-json"
                rows={16}
                value={jsonText}
                onChange={(e) => setJsonText(e.target.value)}
                disabled={submitting}
              />
            ) : (
              <div className="rf-batch">
                <p className="muted">One run per row.</p>
                <input
                  type="file"
                  accept=".csv,.json,text/csv,application/json"
                  disabled={submitting}
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) void onBatchFile(f);
                  }}
                />
                <textarea
                  className="rf-json"
                  rows={12}
                  placeholder={'[{"key": "value"}, …]   — or —   key,other\nv1,v2'}
                  value={batchText}
                  onChange={(e) => setBatchText(e.target.value)}
                  disabled={submitting}
                />
                {batchText.trim() !== '' && <BatchCount text={batchText} />}
              </div>
            )}

            {error && <p className="error">{error}</p>}

            <div className="dialog-actions">
              <button onClick={onClose} disabled={submitting}>
                Cancel
              </button>
              <button
                className="primary"
                onClick={() => void (mode === 'batch' ? submitBatch() : submit())}
                disabled={submitting}
              >
                {submitting
                  ? mode === 'batch'
                    ? 'Running…'
                    : dryRun
                      ? 'Testing…'
                      : 'Running…'
                  : mode === 'batch'
                    ? 'Run batch'
                    : dryRun
                      ? 'Test'
                      : 'Run'}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
