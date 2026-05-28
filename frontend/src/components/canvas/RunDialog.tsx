import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { api, errorMessage } from '../../api/client';
import { hasRole } from '../../lib/auth';
import { emptyLike, fieldKind, removeAt, setIn, type Path } from '../../lib/run-form';
import type { WorkflowDefinition } from '../../types';

type Update = (path: Path, value: unknown) => void;
type Remove = (path: Path) => void;

function ScalarInput({
  kind,
  value,
  onChange,
}: {
  kind: 'string' | 'number' | 'boolean' | 'null';
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  if (kind === 'boolean') {
    return (
      <input
        type="checkbox"
        checked={value === true}
        onChange={(e) => onChange(e.target.checked)}
      />
    );
  }
  if (kind === 'number') {
    return (
      <input
        type="number"
        value={typeof value === 'number' ? value : ''}
        onChange={(e) => onChange(e.target.value === '' ? 0 : Number(e.target.value))}
      />
    );
  }
  const str = value === null || value === undefined ? '' : String(value);
  if (str.length > 60 || str.includes('\n')) {
    return <textarea rows={3} value={str} onChange={(e) => onChange(e.target.value)} />;
  }
  return <input type="text" value={str} onChange={(e) => onChange(e.target.value)} />;
}

function FieldEditor({
  label,
  value,
  path,
  update,
  remove,
}: {
  label: string;
  value: unknown;
  path: Path;
  update: Update;
  remove: Remove;
}) {
  const kind = fieldKind(value);

  if (kind === 'object') {
    return (
      <fieldset className="rf-object">
        {label && <legend>{label}</legend>}
        {Object.entries(value as Record<string, unknown>).map(([k, v]) => (
          <FieldEditor
            key={k}
            label={k}
            value={v}
            path={[...path, k]}
            update={update}
            remove={remove}
          />
        ))}
      </fieldset>
    );
  }

  if (kind === 'array') {
    const arr = value as unknown[];
    return (
      <div className="rf-array">
        <div className="rf-label">{label}</div>
        {arr.map((item, i) => (
          <div className="rf-array-item" key={i}>
            <FieldEditor
              label={`#${i + 1}`}
              value={item}
              path={[...path, i]}
              update={update}
              remove={remove}
            />
            <button type="button" className="link" onClick={() => remove([...path, i])}>
              remove
            </button>
          </div>
        ))}
        <button
          type="button"
          className="link"
          onClick={() => update(path, [...arr, emptyLike(arr[0] ?? '')])}
        >
          + Add another
        </button>
      </div>
    );
  }

  const scalarKind = kind as 'string' | 'number' | 'boolean' | 'null';
  return (
    <label className={`rf-field${scalarKind === 'boolean' ? ' rf-bool' : ''}`}>
      <span className="rf-label">{label}</span>
      <ScalarInput kind={scalarKind} value={value} onChange={(v) => update(path, v)} />
    </label>
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
