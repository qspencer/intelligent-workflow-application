import { useState } from 'react';

import { api, errorMessage } from '../../api/client';

/** Import dialog extracted from the old WorkflowsList (IA_PLAN §4b). */
export function ImportWorkflowDialog({
  onClose,
  onImported,
}: {
  onClose: () => void;
  onImported: () => void;
}) {
  const [text, setText] = useState('');
  const [format, setFormat] = useState<'yaml' | 'json'>('yaml');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function close(): void {
    if (!submitting) onClose();
  }

  async function submit(): Promise<void> {
    const body = text.trim();
    if (!body) return;
    setSubmitting(true);
    setError(null);
    try {
      await api.importWorkflow(body, format);
      setSubmitting(false);
      onImported();
    } catch (err) {
      setSubmitting(false);
      setError(errorMessage(err, 'Import failed'));
    }
  }

  return (
    <div className="dialog-overlay" onClick={close}>
      <div className="dialog large" onClick={(e) => e.stopPropagation()}>
        <h3>Import workflow</h3>
        <p className="muted">Paste a YAML or JSON workflow definition.</p>
        <textarea
          rows={20}
          placeholder={'id: my-workflow\nname: ...'}
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={submitting}
        />
        <label className="format">
          Format:
          <select
            value={format}
            onChange={(e) => setFormat(e.target.value as 'yaml' | 'json')}
            disabled={submitting}
          >
            <option value="yaml">YAML</option>
            <option value="json">JSON</option>
          </select>
        </label>
        {error && <p className="error">{error}</p>}
        <div className="dialog-actions">
          <button onClick={close} disabled={submitting}>
            Cancel
          </button>
          <button className="primary" onClick={() => void submit()} disabled={submitting || !text.trim()}>
            {submitting ? 'Importing…' : 'Import'}
          </button>
        </div>
      </div>
    </div>
  );
}
