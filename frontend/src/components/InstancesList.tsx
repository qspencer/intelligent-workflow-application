import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { api, errorMessage } from '../api/client';
import { fmtShort } from '../lib/format';
import type { WorkflowInstance } from '../types';

type RowAction = 'pause' | 'resume' | 'kill' | 'retry';

const TERMINAL = new Set(['completed', 'failed', 'killed']);
function isTerminal(state: string): boolean {
  return TERMINAL.has(state);
}
function short(id: string): string {
  return id.slice(0, 8);
}

export function InstancesList() {
  const [searchParams] = useSearchParams();
  const workflowId = searchParams.get('workflow_id') ?? undefined;

  const [instances, setInstances] = useState<WorkflowInstance[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  /** Instance ID with an in-flight per-row action; disables that row. */
  const [busyOn, setBusyOn] = useState<string | null>(null);
  const [deletingAll, setDeletingAll] = useState(false);

  // Hold the latest workflowId in a ref so the polling interval (set up once)
  // always queries the current filter without re-subscribing.
  const workflowIdRef = useRef<string | undefined>(workflowId);
  workflowIdRef.current = workflowId;

  const refresh = useCallback(async (): Promise<void> => {
    try {
      const data = await api.listInstances({
        workflow_id: workflowIdRef.current,
        limit: 50,
      });
      setInstances(data);
      setLoading(false);
      setError(null);
    } catch (err) {
      setError(errorMessage(err, 'Failed to load instances'));
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 5000);
    return () => clearInterval(timer);
  }, [refresh, workflowId]);

  const terminalCount = instances.filter((i) => isTerminal(i.state)).length;

  async function deleteInstance(id: string): Promise<void> {
    if (busyOn) return;
    if (!window.confirm(`Delete instance ${short(id)}? This cannot be undone.`)) return;
    setBusyOn(id);
    try {
      await api.deleteInstance(id);
      setInstances((rows) => rows.filter((r) => r.id !== id));
    } catch (err) {
      setError(errorMessage(err, 'Failed to delete instance'));
    } finally {
      setBusyOn(null);
    }
  }

  async function action(id: string, name: RowAction): Promise<void> {
    if (busyOn) return;
    setBusyOn(id);
    try {
      if (name === 'pause') await api.pauseInstance(id);
      else if (name === 'resume') await api.resumeInstance(id);
      else if (name === 'retry') await api.retryInstance(id);
      else await api.killInstance(id);
      setBusyOn(null);
      await refresh();
    } catch (err) {
      setError(errorMessage(err, `Failed to ${name} instance`));
      setBusyOn(null);
    }
  }

  async function deleteAllTerminal(): Promise<void> {
    if (deletingAll) return;
    const n = terminalCount;
    if (n === 0) return;
    if (
      !window.confirm(
        `Delete ${n} completed/failed/killed instance${n === 1 ? '' : 's'}? ` +
          `This cannot be undone. Running, pending, and paused instances are NOT affected.`,
      )
    ) {
      return;
    }
    setDeletingAll(true);
    try {
      await api.deleteInstancesByStates(['completed', 'failed', 'killed']);
      setInstances((rows) => rows.filter((r) => !isTerminal(r.state)));
    } catch (err) {
      setError(errorMessage(err, 'Failed to bulk-delete instances'));
    } finally {
      setDeletingAll(false);
    }
  }

  return (
    <div className="page-instances">
      <div className="header-row">
        <h2>Workflow Instances</h2>
        <button
          className="danger"
          disabled={deletingAll || terminalCount === 0}
          onClick={() => void deleteAllTerminal()}
          title="Delete every instance currently in Completed, Failed, or Killed status. Running, Pending, and Paused instances are not affected."
        >
          {deletingAll ? 'Deleting…' : `Delete Finished (${terminalCount})`}
        </button>
      </div>

      {loading ? (
        <p>Loading…</p>
      ) : error ? (
        <p className="error">{error}</p>
      ) : instances.length === 0 ? (
        <p>No instances yet.</p>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Workflow</th>
                <th>State</th>
                <th>Started</th>
                <th>Finished</th>
                <th className="actions-col"></th>
              </tr>
            </thead>
            <tbody>
              {instances.map((inst) => (
                <tr key={inst.id}>
                  <td>
                    <Link to={`/instances/${inst.id}`}>
                      <code>{short(inst.id)}</code>
                    </Link>
                  </td>
                  <td>{inst.workflow_id}</td>
                  <td>
                    <span className={`badge ${inst.state}`}>{inst.state}</span>
                  </td>
                  <td>{fmtShort(inst.started_at)}</td>
                  <td>{fmtShort(inst.completed_at)}</td>
                  <td className="actions-col">
                    <RowActions
                      inst={inst}
                      busy={busyOn === inst.id}
                      onAction={action}
                      onDelete={deleteInstance}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

interface RowActionsProps {
  inst: WorkflowInstance;
  busy: boolean;
  onAction: (id: string, name: RowAction) => void;
  onDelete: (id: string) => void;
}

function RowActions({ inst, busy, onAction, onDelete }: RowActionsProps) {
  const { id, state } = inst;
  switch (state) {
    case 'running':
      return (
        <>
          <button className="small" disabled={busy} onClick={() => onAction(id, 'pause')} title="Pause this run">
            Pause
          </button>
          <button className="danger small" disabled={busy} onClick={() => onAction(id, 'kill')} title="Kill this run (terminal, not resumable)">
            Kill
          </button>
        </>
      );
    case 'paused':
      return (
        <>
          <button className="small" disabled={busy} onClick={() => onAction(id, 'resume')} title="Resume from where it paused">
            Resume
          </button>
          <button className="danger small" disabled={busy} onClick={() => onAction(id, 'kill')} title="Kill this paused run">
            Kill
          </button>
        </>
      );
    case 'pending':
      return (
        <button className="danger small" disabled={busy} onClick={() => onAction(id, 'kill')} title="Kill before it starts">
          Kill
        </button>
      );
    case 'failed':
      return (
        <>
          <button className="small" disabled={busy} onClick={() => onAction(id, 'retry')} title="Retry from the failed step">
            Retry
          </button>
          <button className="danger small" disabled={busy} onClick={() => onDelete(id)} title="Delete this failed instance">
            Delete
          </button>
        </>
      );
    case 'completed':
      return (
        <button className="danger small" disabled={busy} onClick={() => onDelete(id)} title="Delete this completed instance">
          Delete
        </button>
      );
    case 'killed':
      return (
        <button className="danger small" disabled={busy} onClick={() => onDelete(id)} title="Delete this killed instance">
          Delete
        </button>
      );
    default:
      return null;
  }
}
