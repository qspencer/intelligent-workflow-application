import { useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router';

import { api, errorMessage } from '../api/client';
import { hasRole } from '../lib/auth';
import { useCatalog } from '../hooks/useCatalog';
import type { WorkflowAttribution, WorkflowDefinition, WorkflowState } from '../types';
import { ImportWorkflowDialog } from './dialogs/ImportWorkflowDialog';
import { RunWorkflowDialog } from './dialogs/RunWorkflowDialog';
import { Skeleton } from './Skeleton';

/** Strip markdown noise from a description so it reads cleanly on a card. */
function describe(raw: string | undefined, max = 160): string {
  if (!raw) return '';
  const cleaned = raw
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/\*([^*]+)\*/g, '$1')
    .replace(/^#+\s*/gm, '')
    .replace(/\s+/g, ' ')
    .trim();
  return cleaned.length > max ? cleaned.slice(0, max - 3) + '…' : cleaned;
}

const STATUS_LABEL: Record<WorkflowState, string> = {
  pending: 'Waiting',
  running: 'Running',
  paused: 'Paused',
  completed: 'Done',
  failed: 'Failed',
  killed: 'Stopped',
};

const VIEW_STORAGE_KEY = 'wp.automations.view';

type ViewMode = 'cards' | 'table';

/** The merged Automations catalog (IA_PLAN): one entity, one list, two
 *  renderings. ALL definitions — user-created and bundled — with the view
 *  mode URL-addressable (`?view=cards|table`; localStorage supplies the
 *  default when the param is absent). No template-id filtering: bundled
 *  workflows carry a badge instead of being hidden. */
export function AutomationsHome() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { definitions, attribution, counts, latest, loading, error, refresh } = useCatalog();

  const paramView = searchParams.get('view');
  const view: ViewMode =
    paramView === 'table' || paramView === 'cards'
      ? paramView
      : localStorage.getItem(VIEW_STORAGE_KEY) === 'table'
        ? 'table'
        : 'cards';

  function setView(next: ViewMode): void {
    localStorage.setItem(VIEW_STORAGE_KEY, next);
    setSearchParams(next === 'cards' ? {} : { view: next }, { replace: false });
  }

  const [createOpen, setCreateOpen] = useState(false);
  const [createName, setCreateName] = useState('');
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const [describeOpen, setDescribeOpen] = useState(false);
  const [describeText, setDescribeText] = useState('');
  const [scaffolding, setScaffolding] = useState(false);
  const [describeError, setDescribeError] = useState<string | null>(null);

  const [deleteTarget, setDeleteTarget] = useState<WorkflowDefinition | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const [runTarget, setRunTarget] = useState<WorkflowDefinition | null>(null);
  const [importOpen, setImportOpen] = useState(false);

  const canCreate = hasRole(['admins', 'org-admins', 'org-users']);
  // Org badges are Administrator-only (IA_PLAN: non-admins see only their
  // own org's rows, so the badge carries no information for them).
  const showOrgBadges = hasRole(['admins']);

  function attrOf(id: string): WorkflowAttribution | null {
    return attribution?.[id] ?? null;
  }
  function isBundled(id: string): boolean {
    return attrOf(id)?.source === 'bundled';
  }

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

  function badges(wf: WorkflowDefinition): React.ReactNode {
    const attr = attrOf(wf.id);
    if (!attr) return null;
    return (
      <>
        {attr.source === 'bundled' && (
          <span className="badge bundled" title="Bundled example — managed by the examples directory">
            Bundled
          </span>
        )}
        {showOrgBadges && (
          <span className="badge org" title={`Organization: ${attr.org_name}`}>
            {attr.org_name}
          </span>
        )}
      </>
    );
  }

  const list = definitions ?? [];

  return (
    <div className="page-home">
      <div className="header">
        <h2>Your automations</h2>
        <div className="home-actions">
          <div className="view-toggle" role="group" aria-label="View mode">
            <button
              className={view === 'cards' ? 'active' : ''}
              aria-pressed={view === 'cards'}
              onClick={() => setView('cards')}
            >
              Cards
            </button>
            <button
              className={view === 'table' ? 'active' : ''}
              aria-pressed={view === 'table'}
              onClick={() => setView('table')}
            >
              Table
            </button>
          </div>
          <Link className="button" to="/templates">
            Browse templates
          </Link>
          {canCreate && view === 'table' && (
            <button onClick={() => setImportOpen(true)}>Import</button>
          )}
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
        <Skeleton variant={view === 'cards' ? 'cards' : 'table'} count={3} />
      ) : error ? (
        <p className="error">{error}</p>
      ) : list.length === 0 ? (
        <div className="empty-state">
          <p>No automations yet.</p>
          <p className="muted">
            Start from a <Link to="/templates">template</Link>
            {canCreate ? ' or create one from scratch.' : '.'}
          </p>
        </div>
      ) : view === 'cards' ? (
        <div className="card-grid">
          {list.map((wf) => {
            const state = latest[wf.id];
            const attr = attrOf(wf.id);
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
                    <span>{counts === null ? '— runs' : `${counts[wf.id] || 0} runs`}</span>
                    {badges(wf)}
                    {attr?.owner_display_name && (
                      <span className="muted" title="Owner">
                        {attr.owner_display_name}
                      </span>
                    )}
                  </div>
                </Link>
                {canCreate && (
                  <div className="wf-card-actions">
                    <button
                      className="wf-card-run"
                      title="Run this automation"
                      aria-label={`Run ${wf.name}`}
                      onClick={() => setRunTarget(wf)}
                    >
                      Run
                    </button>
                    {!isBundled(wf.id) && (
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
                )}
              </div>
            );
          })}
        </div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Description</th>
              <th className="num-col">Steps</th>
              <th className="num-col">Runs</th>
              <th className="actions-col">Actions</th>
            </tr>
          </thead>
          <tbody>
            {list.map((wf) => (
              <tr key={wf.id}>
                <td>
                  <Link className="name-cell" to={`/canvas/${wf.id}`} title="Open the workflow canvas">
                    {wf.name}
                  </Link>
                  <code className="muted">{wf.id}</code> {badges(wf)}
                </td>
                <td>
                  <span title={wf.description || ''}>{describe(wf.description, 120) || '—'}</span>
                </td>
                <td className="num-col">{wf.steps?.length ?? 0}</td>
                <td className="num-col">
                  {counts === null ? (
                    <span className="muted">—</span>
                  ) : (
                    <Link
                      to={`/runs?workflow_id=${encodeURIComponent(wf.id)}`}
                      title={`View ${counts[wf.id] || 0} run(s) of this workflow`}
                    >
                      {counts[wf.id] || 0}
                    </Link>
                  )}
                </td>
                <td className="actions-col">
                  {canCreate && <button onClick={() => setRunTarget(wf)}>Run</button>}
                  {canCreate && !isBundled(wf.id) && (
                    <button
                      onClick={() => {
                        setDeleteError(null);
                        setDeleteTarget(wf);
                      }}
                    >
                      Delete
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {runTarget && (
        <RunWorkflowDialog
          workflow={runTarget}
          attribution={attrOf(runTarget.id)}
          onClose={() => setRunTarget(null)}
        />
      )}

      {importOpen && (
        <ImportWorkflowDialog
          onClose={() => setImportOpen(false)}
          onImported={() => {
            setImportOpen(false);
            void refresh();
          }}
        />
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
