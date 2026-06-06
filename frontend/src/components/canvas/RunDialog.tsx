import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { api, errorMessage } from '../../api/client';
import { hasRole } from '../../lib/auth';
import { removeAt, setIn } from '../../lib/run-form';
import type { CostEstimate, WorkflowDefinition } from '../../types';
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

function initialPayload(def: WorkflowDefinition): Record<string, unknown> {
  const ex = def.trigger?.example_payload;
  return ex && Object.keys(ex).length > 0 ? structuredClone(ex) : {};
}

export function RunDialog({ def, onClose }: { def: WorkflowDefinition; onClose: () => void }) {
  const navigate = useNavigate();
  const seeded = initialPayload(def);
  const hasFields = Object.keys(seeded).length > 0;

  const [value, setValue] = useState<Record<string, unknown>>(seeded);
  // No example payload to build a form from → default to JSON entry.
  const [mode, setMode] = useState<'form' | 'json'>(hasFields ? 'form' : 'json');
  const [jsonText, setJsonText] = useState(JSON.stringify(seeded, null, 2));
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

  function switchMode(next: 'form' | 'json'): void {
    if (next === mode) return;
    if (next === 'json') {
      setJsonText(JSON.stringify(value, null, 2));
    } else {
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
      const res = await api.runWorkflow(def.id, payload as Record<string, unknown>);
      onClose();
      // Flip the canvas into live (instance) mode — the C2 overlay takes over.
      navigate(`/canvas/${def.id}?instance=${res.instance_id}`);
    } catch (err) {
      setSubmitting(false);
      setError(errorMessage(err, 'Run failed'));
    }
  }

  return (
    <div className="dialog-overlay" onClick={() => !submitting && onClose()}>
      <div className="dialog large" onClick={(e) => e.stopPropagation()}>
        <h3>
          Run <code>{def.id}</code>
        </h3>
        <p className="muted">
          Fill in the trigger details and run. The canvas will switch to a live view of
          the run.
          {!hasRole(['admins', 'operators']) && <span> Operator or Admin role required.</span>}
        </p>

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
        ) : (
          <textarea
            className="rf-json"
            rows={16}
            value={jsonText}
            onChange={(e) => setJsonText(e.target.value)}
            disabled={submitting}
          />
        )}

        {error && <p className="error">{error}</p>}

        <div className="dialog-actions">
          <button onClick={onClose} disabled={submitting}>
            Cancel
          </button>
          <button className="primary" onClick={() => void submit()} disabled={submitting}>
            {submitting ? 'Running…' : 'Run'}
          </button>
        </div>
      </div>
    </div>
  );
}
