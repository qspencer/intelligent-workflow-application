import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';

import { api, errorMessage } from '../api/client';
import { extractEvaluations, scoreClass } from '../lib/evaluation';
import { extractRecall, summarizeMemory } from '../lib/memory';
import { fmtMedium, fmtShort, fmtShortTime } from '../lib/format';
import {
  costSkew,
  extractUsage,
  formatUsage,
  usageTooltip,
} from '../lib/usage';
import { useEvents } from '../hooks/useEvents';
import { Skeleton } from './Skeleton';
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
  const [siblings, setSiblings] = useState<WorkflowInstance[]>([]);

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
      const fresh = await api.instanceAudit(id);
      // Merge, don't replace: a poll that started before a WS-pushed entry
      // arrived must not make that entry vanish until the next poll.
      setAuditEntries((current) => {
        const ids = new Set(fresh.map((e) => e.id));
        const extras = current.filter((e) => !ids.has(e.id));
        return extras.length > 0 ? [...fresh, ...extras] : fresh;
      });
    } catch {
      // Auditor/Admin role required; keep whatever the WS stream delivered.
    }
  }, [id]);

  // Poll fast while the run is live; once terminal, drop to a slow heartbeat
  // (retry/resume can revive a terminal run — the state change re-arms the
  // fast cadence, and lifecycle buttons call refresh() directly).
  const instanceTerminal =
    instance != null && ['completed', 'failed', 'killed'].includes(instance.state);
  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), instanceTerminal ? 30_000 : 3000);
    return () => clearInterval(timer);
  }, [refresh, instanceTerminal]);

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

  // Sibling runs of the same workflow, for the Compare picker (G5).
  useEffect(() => {
    if (!instance) return;
    let ignore = false;
    api
      .listInstances({ workflow_id: instance.workflow_id, limit: 20 })
      .then((list) => {
        if (!ignore) setSiblings(list.filter((i) => i.id !== instance.id));
      })
      .catch(() => {
        /* picker just stays empty */
      });
    return () => {
      ignore = true;
    };
  }, [instance]);

  const evaluations = extractEvaluations(steps);
  const memory = summarizeMemory(auditEntries);

  return (
    <div className="page-instance-detail">
      <p>
        <Link to="/instances">← All instances</Link>
      </p>

      {loading ? (
        <Skeleton variant="detail" count={5} />
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
            <Link className="canvas-link" to={`/canvas/${instance.workflow_id}?instance=${instance.id}`}>
              View on canvas
            </Link>
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
            {siblings.length > 0 && (
              <label className="compare-picker">
                Compare with{' '}
                <select
                  value=""
                  onChange={(e) => {
                    if (e.target.value) navigate(`/compare/${instance.id}/${e.target.value}`);
                  }}
                >
                  <option value="">— pick a run —</option>
                  {siblings.map((s) => (
                    <option key={s.id} value={s.id}>
                      {short(s.id)} · {s.state} · {fmtShort(s.started_at ?? s.created_at)}
                    </option>
                  ))}
                </select>
              </label>
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

          {memory && (
            <>
              <h3>Learned memory</h3>
              <div className="memory-panel">
                {memory.recalls.map((r, i) => (
                  <div className="memory-row" key={`recall-${i}`}>
                    <span className="memory-kind recalled">recalled</span>
                    <span>
                      {r.injected
                        ? `${r.edges} fact${r.edges === 1 ? '' : 's'} + ${r.episodes} episode${
                            r.episodes === 1 ? '' : 's'
                          } about `
                        : 'no prior history for '}
                      <code>{r.query}</code>
                    </span>
                  </div>
                ))}
                {memory.observed.writes > 0 && (
                  <div className="memory-row">
                    <span className="memory-kind observed">observed</span>
                    <span>
                      {memory.observed.writes} write{memory.observed.writes === 1 ? '' : 's'} —{' '}
                      {memory.observed.facts} fact{memory.observed.facts === 1 ? '' : 's'},{' '}
                      {memory.observed.quarantined} quarantined claim
                      {memory.observed.quarantined === 1 ? '' : 's'} ($
                      {memory.observed.costUsd.toFixed(4)})
                    </span>
                  </div>
                )}
                {memory.outcomes.map((o, i) => (
                  <div className="memory-row" key={`outcome-${i}`}>
                    <span className="memory-kind outcome">{o.outcome}</span>
                    <span>
                      via {o.emitter}
                      {o.correctedValue ? (
                        <>
                          {' '}
                          → <code>{o.correctedValue}</code>
                        </>
                      ) : null}
                    </span>
                  </div>
                ))}
                {memory.failures > 0 && (
                  <div className="memory-row">
                    <span className="memory-kind failed">degraded</span>
                    <span>
                      {memory.failures} memory operation{memory.failures === 1 ? '' : 's'} failed
                      (run unaffected)
                    </span>
                  </div>
                )}
              </div>
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
                <th>Recall</th>
                <th>Error</th>
                <th>Fork</th>
              </tr>
            </thead>
            <tbody>
              {steps.map((s) => {
                const u = extractUsage(s);
                const mh = memoryHash(s);
                const recall = extractRecall(s);
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
                    <td>
                      {recall ? (
                        <code
                          className="recall"
                          title={`Recalled history for ${recall.query}` +
                            (recall.contextHash ? ` (${recall.contextHash})` : '')}
                        >
                          {recall.edges}e·{recall.episodes}ep
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
