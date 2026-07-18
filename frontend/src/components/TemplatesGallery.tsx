import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import { api, errorMessage } from '../api/client';
import { hasRole } from '../lib/auth';
import type { WorkflowTemplate } from '../types';
import { Skeleton } from './Skeleton';

const TRIGGER_LABEL: Record<string, string> = {
  filesystem: 'On a new file',
  schedule: 'On a schedule',
  webhook: 'On a webhook',
  email: 'On an email',
  gmail_poll: 'On an email', // legacy alias for email
  manual: 'Run manually',
};

function triggerLabel(type: string): string {
  return TRIGGER_LABEL[type] ?? type;
}

/** Templates gallery (canvas roadmap C5.2). Seeds from the bundled example
 *  workflows; "Use this template" clones one into a new editable workflow. */
export function TemplatesGallery() {
  const navigate = useNavigate();

  const [templates, setTemplates] = useState<WorkflowTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [usingId, setUsingId] = useState<string | null>(null);

  const canUse = hasRole(['admins', 'org-admins', 'org-users']);

  const refresh = useCallback(async (): Promise<void> => {
    setLoading(true);
    try {
      setTemplates(await api.listTemplates());
      setError(null);
    } catch (err) {
      setError(errorMessage(err, 'Failed to load templates'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function use(template: WorkflowTemplate): Promise<void> {
    setUsingId(template.id);
    setError(null);
    try {
      const def = await api.createWorkflow({ template_id: template.id });
      navigate(`/canvas/${def.id}?edit=1`);
    } catch (err) {
      setUsingId(null);
      setError(errorMessage(err, 'Could not create from template'));
    }
  }

  return (
    <div className="page-templates">
      <div className="header">
        <h2>Templates</h2>
        <Link className="button" to="/">
          ← Back to automations
        </Link>
      </div>
      <p className="muted">Start from a ready-made workflow and tailor it to your needs.</p>

      {loading ? (
        <Skeleton variant="cards" count={6} />
      ) : error ? (
        <p className="error">{error}</p>
      ) : templates.length === 0 ? (
        <p>No templates available.</p>
      ) : (
        <div className="card-grid">
          {templates.map((t) => (
            <div key={t.id} className="wf-card template-card">
              <div className="wf-card-head">
                <span className="wf-card-name">{t.name}</span>
                <span className="trigger-tag">{triggerLabel(t.trigger_type)}</span>
              </div>
              <p className="wf-card-desc">{t.description || '—'}</p>
              <div className="wf-card-meta">
                <span>{t.step_count} steps</span>
                {canUse && (
                  <button
                    className="primary small"
                    onClick={() => void use(t)}
                    disabled={usingId !== null}
                  >
                    {usingId === t.id ? 'Creating…' : 'Use this template'}
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
