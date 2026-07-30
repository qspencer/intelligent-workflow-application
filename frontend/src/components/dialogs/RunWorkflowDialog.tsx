import { useRef, useState } from 'react';
import { useNavigate } from 'react-router';

import { api, errorMessage } from '../../api/client';
import type { WorkflowAttribution, WorkflowDefinition } from '../../types';

interface BatchProgress {
  done: number;
  errors: number;
  total: number;
}

/** Run dialog extracted from the old WorkflowsList (IA_PLAN §4b/§4e).
 *  Single + batch modes; workflows classified mutating/unknown get an
 *  effect warning naming the tools and require an explicit confirmation
 *  checkbox after the payload is composed. Read-only workflows show no
 *  warning — a false destructive warning erodes the real ones. */
export function RunWorkflowDialog({
  workflow,
  attribution,
  onClose,
}: {
  workflow: WorkflowDefinition;
  attribution: WorkflowAttribution | null;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const example = workflow.trigger?.example_payload;
  const [payloadText, setPayloadText] = useState(
    example && Object.keys(example).length > 0 ? JSON.stringify(example, null, 2) : '{}',
  );
  const [batchMode, setBatchMode] = useState(false);
  const [effectConfirmed, setEffectConfirmed] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [progress, setProgress] = useState<BatchProgress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const openRef = useRef(true);

  // Attribution unavailable (degraded per IA_PLAN §6) counts as unknown →
  // treated as mutating: conservative default.
  const mutating = attribution === null || attribution.run_effect !== 'read_only';
  const effectTools = attribution?.effect_tools ?? [];

  function close(): void {
    if (submitting) return;
    openRef.current = false;
    onClose();
  }

  function onBatchModeChange(batch: boolean): void {
    setBatchMode(batch);
    const trimmed = payloadText.trim();
    try {
      const parsed = trimmed ? JSON.parse(trimmed) : null;
      if (batch && parsed && !Array.isArray(parsed) && typeof parsed === 'object') {
        setPayloadText(JSON.stringify([parsed], null, 2));
      } else if (!batch && Array.isArray(parsed) && parsed.length > 0) {
        setPayloadText(JSON.stringify(parsed[0], null, 2));
      } else if (batch && !parsed) {
        setPayloadText(
          example && Object.keys(example).length > 0 ? JSON.stringify([example], null, 2) : '[]',
        );
      }
    } catch {
      // Mid-edit invalid JSON — leave it alone.
    }
    setError(null);
    setProgress(null);
  }

  function submit(): void {
    const text = payloadText.trim() || (batchMode ? '[]' : '{}');
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch (e) {
      setError(`Invalid JSON: ${(e as Error).message}`);
      return;
    }
    if (batchMode) void fireBatch(parsed);
    else void fireSingle(parsed);
  }

  async function fireSingle(parsed: unknown): Promise<void> {
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      setError('Trigger payload must be a JSON object.');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const res = await api.runWorkflow(workflow.id, parsed as Record<string, unknown>);
      setSubmitting(false);
      onClose();
      navigate(`/instances/${res.instance_id}`);
    } catch (err) {
      setSubmitting(false);
      setError(errorMessage(err, 'Run failed'));
    }
  }

  async function fireBatch(parsed: unknown): Promise<void> {
    if (!Array.isArray(parsed)) {
      setError('Batch mode requires a JSON array.');
      return;
    }
    if (parsed.length === 0) {
      setError('Batch array is empty.');
      return;
    }
    const bad = parsed.findIndex((p) => !p || typeof p !== 'object' || Array.isArray(p));
    if (bad >= 0) {
      setError(`Element ${bad} is not a JSON object.`);
      return;
    }
    const total = parsed.length;
    setSubmitting(true);
    setError(null);
    setProgress({ done: 0, errors: 0, total });
    let done = 0;
    let errors = 0;
    for (const payload of parsed as Record<string, unknown>[]) {
      try {
        await api.runWorkflow(workflow.id, payload);
      } catch {
        errors += 1;
      }
      done += 1;
      setProgress({ done, errors, total });
    }
    setSubmitting(false);
    if (errors === 0) {
      setTimeout(() => {
        if (openRef.current) {
          onClose();
          navigate(`/runs?workflow_id=${encodeURIComponent(workflow.id)}`);
        }
      }, 800);
    }
  }

  const batchDone = progress !== null && progress.done === progress.total;

  return (
    <div className="dialog-overlay" onClick={close}>
      <div className="dialog large" onClick={(e) => e.stopPropagation()}>
        <h3>
          Run <code>{workflow.id}</code>
        </h3>
        <p className="muted">
          {batchMode
            ? 'JSON array — fires one workflow instance per array element.'
            : 'JSON object passed verbatim as the trigger payload.'}
        </p>
        {mutating && (
          <p className="effect-warning" role="note">
            ⚠ This workflow acts on external systems
            {effectTools.length > 0 && (
              <>
                {' '}
                via: <code>{effectTools.join(', ')}</code>
              </>
            )}
            . Runs make real changes.
          </p>
        )}
        <label className="mode-toggle">
          <input
            type="checkbox"
            checked={batchMode}
            onChange={(e) => onBatchModeChange(e.target.checked)}
            disabled={submitting}
          />
          Batch mode (paste a JSON array; one instance per element)
        </label>
        <textarea
          rows={16}
          placeholder={batchMode ? '[{...}, {...}]' : '{"file_path": "/abs/path/to/some.pdf"}'}
          value={payloadText}
          onChange={(e) => setPayloadText(e.target.value)}
          disabled={submitting}
        />
        {mutating && (
          <label className="mode-toggle effect-confirm">
            <input
              type="checkbox"
              checked={effectConfirmed}
              onChange={(e) => setEffectConfirmed(e.target.checked)}
              disabled={submitting}
            />
            I understand this run changes external systems
          </label>
        )}
        {progress && (
          <p className="muted">
            {progress.done < progress.total ? (
              <>
                Firing {progress.done + 1} of {progress.total}…
                {progress.errors > 0 && (
                  <span className="error">
                    {' '}
                    ({progress.errors} error{progress.errors === 1 ? '' : 's'} so far)
                  </span>
                )}
              </>
            ) : (
              <>
                Fired {progress.done - progress.errors} of {progress.total}.
                {progress.errors > 0 && <span className="error"> {progress.errors} failed.</span>}
              </>
            )}
          </p>
        )}
        {error && <p className="error">{error}</p>}
        <div className="dialog-actions">
          <button onClick={close} disabled={submitting}>
            {batchDone ? 'Close' : 'Cancel'}
          </button>
          <button
            className="primary"
            onClick={submit}
            disabled={submitting || batchDone || (mutating && !effectConfirmed)}
          >
            {submitting ? (batchMode ? 'Firing…' : 'Running…') : batchMode ? 'Fire batch' : 'Run'}
          </button>
        </div>
      </div>
    </div>
  );
}
