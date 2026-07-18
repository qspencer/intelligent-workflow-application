import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import { api, errorMessage } from '../api/client';
import { hasRole } from '../lib/auth';
import type { WorkflowDefinition, WorkflowState } from '../types';
import { Skeleton } from './Skeleton';

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

  // C7.1 NL scaffold — "Describe it".
  const [describeOpen, setDescribeOpen] = useState(false);
  const [describeText, setDescribeText] = useState('');
  const [scaffolding, setScaffolding] = useState(false);
  const [describeError, setDescribeError] = useState<string | null>(null);

  // Delete a workflow (definition + run history).
  const [deleteTarget, setDeleteTarget] = useState<WorkflowDefinition | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const canCreate = hasRole(['admins', 'designers']);

  const refresh = useCallback(async (): Promise<void> => {
    setLoading(true);
    // The trigger orchestrator registers the bundled examples as runnable
    // workflows so their triggers fire — but on this friendly home they belong
    // in Templates, not "your automations". Exclude any workflow whose id is a
    // template id. They remain in Templates and the dev-console Workflows list.
    // Best-effort: if templates can't load we just don't filter.
    let templateIds = new Set<string>();
    try {
      templateIds = new Set((await api.listTemplates()).map((t) => t.id));
    } catch {
      templateIds = new Set();
    }
    try {
      const defs = await api.listWorkflows();
      setDefinitions(defs.filter((d) => !templateIds.has(d.id)));
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

  async function submitDelete(): Promise<void> {
    if (!deleteTarget) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await api.deleteWorkflow(deleteTarget.id);
      setDeleting(false);
      setDeleteTarget(null);
      await refresh();
    } catch (err) {
      setDeleting(false);
      setDeleteError(errorMessage(err, 'Could not delete automation'));
    }
  }

  async function submitDescribe(): Promise<void> {
    const text = describeText.trim();
    if (!text) return;
    setScaffolding(true);
    setDescribeError(null);
    try {
      const result = await api.scaffoldWorkflow(text);
      setScaffolding(false);
      setDescribeOpen(false);
      navigate(`/canvas/${result.workflow_id}?edit=1`);
    } catch (err) {
      setScaffolding(false);
      setDescribeError(errorMessage(err, 'Could not draft a workflow from that description'));
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
              onClick={() => {
                setDescribeText('');
                setDescribeError(null);
                setDescribeOpen(true);
              }}
            >
              Describe it
            </button>
          )}
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
        <Skeleton variant="cards" count={3} />
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
              <div key={wf.id} className="wf-card">
                <Link className="wf-card-link" to={`/canvas/${wf.id}`}>
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
                {canCreate && (
                  <button
                    className="wf-card-delete"
                    title="Delete automation"
                    aria-label={`Delete ${wf.name}`}
                    onClick={() => {
                      setDeleteError(null);
                      setDeleteTarget(wf);
                    }}
                  >
                    ✕
                  </button>
                )}
              </div>
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

      {describeOpen && (
        <div className="dialog-overlay" onClick={() => !scaffolding && setDescribeOpen(false)}>
          <div className="dialog" onClick={(e) => e.stopPropagation()}>
            <h3>Describe your automation</h3>
            <p className="muted">
              Say what it should do in plain English. We'll draft the steps — you refine them on the
              next screen.
            </p>
            <textarea
              autoFocus
              rows={5}
              placeholder="e.g. When a PDF lands in my inbox folder, pull out the text, classify it, and file it into a folder by type."
              value={describeText}
              onChange={(e) => setDescribeText(e.target.value)}
              disabled={scaffolding}
            />
            {describeError && <p className="error">{describeError}</p>}
            <div className="dialog-actions">
              <button onClick={() => setDescribeOpen(false)} disabled={scaffolding}>
                Cancel
              </button>
              <button
                className="primary"
                onClick={() => void submitDescribe()}
                disabled={scaffolding || !describeText.trim()}
              >
                {scaffolding ? 'Drafting…' : 'Draft it'}
              </button>
            </div>
          </div>
        </div>
      )}

      {deleteTarget && (
        <div className="dialog-overlay" onClick={() => !deleting && setDeleteTarget(null)}>
          <div className="dialog" onClick={(e) => e.stopPropagation()}>
            <h3>Delete automation</h3>
            <p>
              Delete <strong>{deleteTarget.name}</strong>? This removes the workflow and its run
              history. This can't be undone.
            </p>
            {deleteError && <p className="error">{deleteError}</p>}
            <div className="dialog-actions">
              <button onClick={() => setDeleteTarget(null)} disabled={deleting}>
                Cancel
              </button>
              <button className="danger" onClick={() => void submitDelete()} disabled={deleting}>
                {deleting ? 'Deleting…' : 'Delete'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
