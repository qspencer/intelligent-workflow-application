import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import { api, errorMessage } from '../api/client';
import { hasRole } from '../lib/auth';
import type { WorkflowDefinition, WorkflowState } from '../types';

/** Strip markdown noise from a description so it reads cleanly on a card. */
function describe(raw: string | undefined): string {
  if (!raw) return '';
  const cleaned = raw
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/\*([^*]+)\*/g, '$1')
    .replace(/^#+\s*/gm, '')
    .replace(/\s+/g, ' ')
    .trim();
  return cleaned.length > 160 ? cleaned.slice(0, 157) + '…' : cleaned;
}

const STATUS_LABEL: Record<WorkflowState, string> = {
  pending: 'Waiting',
  running: 'Running',
  paused: 'Paused',
  completed: 'Done',
  failed: 'Failed',
  killed: 'Stopped',
};

/** The friendly landing surface (canvas roadmap C5.1). A card grid of the
 *  user's automations with a clear path to create one — no UUIDs, no JSON. */
export function AutomationsHome() {
  const navigate = useNavigate();

  const [definitions, setDefinitions] = useState<WorkflowDefinition[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [latest, setLatest] = useState<Record<string, WorkflowState>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [createOpen, setCreateOpen] = useState(false);
  const [createName, setCreateName] = useState('');
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const canCreate = hasRole(['admins', 'designers']);

  const refresh = useCallback(async (): Promise<void> => {
    setLoading(true);
    try {
      setDefinitions(await api.listWorkflows());
      setError(null);
    } catch (err) {
      setError(errorMessage(err, 'Failed to load automations'));
    } finally {
      setLoading(false);
    }
    // Counts + latest run state are best-effort enrichments.
    try {
      setCounts(await api.workflowInstanceCounts());
    } catch {
      setCounts({});
    }
    try {
      const recent = await api.listInstances({ limit: 200 });
      const byWorkflow: Record<string, WorkflowState> = {};
      // listInstances returns newest-first; first seen per workflow is latest.
      for (const inst of recent) {
        if (!(inst.workflow_id in byWorkflow)) byWorkflow[inst.workflow_id] = inst.state;
      }
      setLatest(byWorkflow);
    } catch {
      setLatest({});
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function submitCreate(): Promise<void> {
    setCreating(true);
    setCreateError(null);
    try {
      const def = await api.createWorkflow(createName.trim() ? { name: createName.trim() } : {});
      setCreating(false);
      setCreateOpen(false);
      navigate(`/canvas/${def.id}?edit=1`);
    } catch (err) {
      setCreating(false);
      setCreateError(errorMessage(err, 'Could not create workflow'));
    }
  }

  return (
    <div className="page-home">
      <div className="header">
        <h2>Your automations</h2>
        <div className="home-actions">
          <Link className="button" to="/templates">
            Browse templates
          </Link>
          {canCreate && (
            <button
              className="primary"
              onClick={() => {
                setCreateName('');
                setCreateError(null);
                setCreateOpen(true);
              }}
            >
              Create
            </button>
          )}
        </div>
      </div>

      {loading ? (
        <p>Loading…</p>
      ) : error ? (
        <p className="error">{error}</p>
      ) : definitions.length === 0 ? (
        <div className="empty-state">
          <p>No automations yet.</p>
          <p className="muted">
            Start from a <Link to="/templates">template</Link>
            {canCreate ? ' or create one from scratch.' : '.'}
          </p>
        </div>
      ) : (
        <div className="card-grid">
          {definitions.map((wf) => {
            const state = latest[wf.id];
            return (
              <Link key={wf.id} className="wf-card" to={`/canvas/${wf.id}`}>
                <div className="wf-card-head">
                  <span className="wf-card-name">{wf.name}</span>
                  {state && (
                    <span className={`status-pill status-${state}`}>{STATUS_LABEL[state]}</span>
                  )}
                </div>
                <p className="wf-card-desc">{describe(wf.description) || '—'}</p>
                <div className="wf-card-meta">
                  <span>{wf.steps?.length ?? 0} steps</span>
                  <span>{counts[wf.id] || 0} runs</span>
                </div>
              </Link>
            );
          })}
        </div>
      )}

      {createOpen && (
        <div className="dialog-overlay" onClick={() => !creating && setCreateOpen(false)}>
          <div className="dialog" onClick={(e) => e.stopPropagation()}>
            <h3>Create automation</h3>
            <p className="muted">Give it a name. You'll add steps on the next screen.</p>
            <input
              type="text"
              autoFocus
              placeholder="e.g. Invoice triage"
              value={createName}
              onChange={(e) => setCreateName(e.target.value)}
              disabled={creating}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void submitCreate();
              }}
            />
            {createError && <p className="error">{createError}</p>}
            <div className="dialog-actions">
              <button onClick={() => setCreateOpen(false)} disabled={creating}>
                Cancel
              </button>
              <button className="primary" onClick={() => void submitCreate()} disabled={creating}>
                {creating ? 'Creating…' : 'Create'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
