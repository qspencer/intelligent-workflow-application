import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';

import { api, errorMessage } from '../api/client';
import { extractEvaluations, scoreClass } from '../lib/evaluation';
import { fmtMedium, fmtShort, fmtShortTime } from '../lib/format';
import {
  costSkew,
  extractUsage,
  formatUsage,
  usageTooltip,
} from '../lib/usage';
import { useEvents } from '../hooks/useEvents';
import type {
  AuditEntry,
  StepExecution,
  WorkflowInstance,
} from '../types';

type Action = 'pause' | 'resume' | 'retry' | 'kill';

function short(id: string): string {
  return id.slice(0, 8);
}
function shortHash(hash: string): string {
  const stripped = hash.startsWith('sha256:') ? hash.slice(7) : hash;
  return stripped.slice(0, 8);
}
function memoryHash(s: StepExecution): string | null {
  const value = s.output?.['memory_hash'];
  return typeof value === 'string' ? value : null;
}

export function InstanceDetail() {
  const { id = '' } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const [instance, setInstance] = useState<WorkflowInstance | null>(null);
  const [steps, setSteps] = useState<StepExecution[]>([]);
  const [auditEntries, setAuditEntries] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [forking, setForking] = useState(false);

  const refresh = useCallback(async (): Promise<void> => {
    try {
      const data = await api.getInstance(id);
      setInstance(data.instance);
      setSteps(data.steps);
      setLoading(false);
      setError(null);
    } catch (err) {
      setError(errorMessage(err, 'Failed to load instance'));
      setLoading(false);
    }
    try {
      setAuditEntries(await api.instanceAudit(id));
    } catch {
      // Auditor/Admin role required; ignore silently for others.
      setAuditEntries([]);
    }
  }, [id]);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 3000);
    return () => clearInterval(timer);
  }, [refresh]);

  // Live audit-event stream; append matching entries, deduped by id.
  useEvents(
    useCallback(
      (entry: AuditEntry) => {
        if (entry.workflow_instance_id !== id) return;
        setAuditEntries((current) =>
          current.some((e) => e.id === entry.id) ? current : [...current, entry],
        );
      },
      [id],
    ),
  );

  async function forkFrom(stepId: string): Promise<void> {
    if (forking) return;
    setForking(true);
    try {
      const res = await api.forkInstance(id, stepId);
      setForking(false);
      navigate(`/instances/${res.instance_id}`);
    } catch (err) {
      setForking(false);
      setError(errorMessage(err, 'Fork failed'));
    }
  }

  async function action(name: Action): Promise<void> {
    try {
      if (name === 'pause') await api.pauseInstance(id);
      else if (name === 'resume') await api.resumeInstance(id);
      else if (name === 'retry') await api.retryInstance(id);
      else await api.killInstance(id);
      await refresh();
    } catch (err) {
      setError(errorMessage(err, 'Action failed'));
    }
  }

  const evaluations = extractEvaluations(steps);

  return (
    <div className="page-instance-detail">
      <p>
        <Link to="/instances">← All instances</Link>
      </p>

      {loading ? (
        <p>Loading…</p>
      ) : error ? (
        <p className="error">{error}</p>
      ) : instance ? (
        <>
          <h2>
            Instance <code>{short(instance.id)}</code>
            <span className={`badge ${instance.state}`}>{instance.state}</span>
          </h2>

          <div className="meta">
            <div>
              <strong>Workflow:</strong> {instance.workflow_id}
            </div>
            <div>
              <strong>Created:</strong> {fmtMedium(instance.created_at)}
            </div>
            {instance.started_at && (
              <div>
                <strong>Started:</strong> {fmtMedium(instance.started_at)}
              </div>
            )}
            {instance.completed_at && (
              <div>
                <strong>Finished:</strong> {fmtMedium(instance.completed_at)}
              </div>
            )}
            {instance.error && (
              <div className="error">
                <strong>Error:</strong> {instance.error}
              </div>
            )}
          </div>

          <div className="actions">
            {instance.state === 'running' && (
              <>
                <button onClick={() => void action('pause')}>Pause</button>
                <button className="danger" onClick={() => void action('kill')}>
                  Kill
                </button>
              </>
            )}
            {instance.state === 'paused' && (
              <>
                <button onClick={() => void action('resume')}>Resume</button>
                <button className="danger" onClick={() => void action('kill')}>
                  Kill
                </button>
              </>
            )}
            {instance.state === 'failed' && (
              <button onClick={() => void action('retry')}>Retry</button>
            )}
          </div>

          {evaluations.length > 0 && (
            <>
              <h3>Evaluation</h3>
              {evaluations.map((e) => (
                <div className="eval" key={e.step_id}>
                  <div className="eval-head">
                    <span className="step">{e.step_id}</span>
                    {!e.parse_ok && <span className="badge failed">parse failed</span>}
                  </div>
                  {e.parse_ok ? (
                    <>
                      <div className="scores">
                        {e.faithfulness_score !== undefined && (
                          <div className="score">
                            <span className="label">Faithfulness</span>
                            <span className={`value ${scoreClass(e.faithfulness_score)}`}>
                              {e.faithfulness_score} / 5
                            </span>
                          </div>
                        )}
                        {e.category_score !== undefined && (
                          <div className="score">
                            <span className="label">Category</span>
                            <span className={`value ${scoreClass(e.category_score)}`}>
                              {e.category_score} / 5
                            </span>
                          </div>
                        )}
                      </div>
                      {e.reasoning && <div className="reasoning">{e.reasoning}</div>}
                      {e.issues && e.issues.length > 0 && (
                        <ul className="issues">
                          {e.issues.map((issue) => (
                            <li key={issue}>{issue}</li>
                          ))}
                        </ul>
                      )}
                    </>
                  ) : (
                    e.raw && <pre className="raw">{e.raw}</pre>
                  )}
                </div>
              ))}
            </>
          )}

          <h3>Steps</h3>
          <table>
            <thead>
              <tr>
                <th>Step</th>
                <th>State</th>
                <th>Started</th>
                <th>Finished</th>
                <th>Usage</th>
                <th>Memory</th>
                <th>Error</th>
                <th>Fork</th>
              </tr>
            </thead>
            <tbody>
              {steps.map((s) => {
                const u = extractUsage(s);
                const mh = memoryHash(s);
                return (
                  <tr key={s.id}>
                    <td>{s.step_id}</td>
                    <td>
                      <span className={`badge ${s.state}`}>{s.state}</span>
                    </td>
                    <td>{fmtShort(s.started_at)}</td>
                    <td>{fmtShort(s.completed_at)}</td>
                    <td>
                      {u ? (
                        <code className={`usage ${costSkew(u)}`} title={usageTooltip(u)}>
                          {formatUsage(u)}
                        </code>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td>
                      {mh ? (
                        <code className="mh" title={mh}>
                          {shortHash(mh)}
                        </code>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td className="error">{s.error ?? ''}</td>
                    <td>
                      <button
                        className="link"
                        disabled={forking}
                        title={
                          `Fork from this step — preserves outputs of every step before ${s.step_id}` +
                          `, re-runs ${s.step_id} and everything downstream with current memory.`
                        }
                        onClick={() => void forkFrom(s.step_id)}
                      >
                        fork
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          <h3>Audit log</h3>
          {auditEntries.length === 0 ? (
            <p className="muted">(none — needs Admin or Auditor role)</p>
          ) : (
            <ul className="audit">
              {auditEntries.map((e) => (
                <li key={e.id}>
                  <span className="when">{fmtShortTime(e.timestamp)}</span>
                  <span className="action">{e.action}</span>
                  {e.step_id && <span className="step">({e.step_id})</span>}
                </li>
              ))}
            </ul>
          )}
        </>
      ) : null}
    </div>
  );
}
