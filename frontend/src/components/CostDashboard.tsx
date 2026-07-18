import { useEffect, useState } from 'react';

import { api, errorMessage } from '../api/client';
import { fmtNum, fmtUsd } from '../lib/format';
import type { CostRowByDay, CostRowByModel, CostRowByWorkflow } from '../types';

type Window = 'all' | '24h' | '7d' | '30d';

/** Translate the UI's window value into an ISO `since` string. */
function sinceParam(window: Window): string | undefined {
  if (window === 'all') return undefined;
  const hours = { '24h': 24, '7d': 24 * 7, '30d': 24 * 30 }[window];
  return new Date(Date.now() - hours * 3_600_000).toISOString();
}

export function CostDashboard() {
  const [window, setWindow] = useState<Window>('all');
  const [byWorkflow, setByWorkflow] = useState<CostRowByWorkflow[]>([]);
  const [byModel, setByModel] = useState<CostRowByModel[]>([]);
  const [byDay, setByDay] = useState<CostRowByDay[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const since = sinceParam(window);
    setLoading(true);
    setError(null);

    Promise.allSettled([
      api.costByWorkflow(since),
      api.costByModel(since),
      api.costByDay(since),
    ]).then((results) => {
      if (cancelled) return;
      const [wf, model, day] = results;
      if (wf.status === 'fulfilled') setByWorkflow(wf.value);
      if (model.status === 'fulfilled') setByModel(model.value);
      if (day.status === 'fulfilled') setByDay(day.value);
      const failed = results.find((r) => r.status === 'rejected');
      if (failed && failed.status === 'rejected') {
        setError(errorMessage(failed.reason, 'Failed to load cost report'));
      }
      setLoading(false);
    });

    return () => {
      cancelled = true;
    };
  }, [window]);

  const totals = byWorkflow.reduce(
    (acc, r) => ({
      cost: acc.cost + r.total_cost_usd,
      tokens: acc.tokens + r.total_tokens,
      steps: acc.steps + r.step_count,
    }),
    { cost: 0, tokens: 0, steps: 0 },
  );

  return (
    <div className="page-cost">
      <h2>Cost Dashboard</h2>

      <div className="filter">
        <label htmlFor="since">Time range:</label>
        <select
          id="since"
          value={window}
          onChange={(e) => setWindow(e.target.value as Window)}
        >
          <option value="all">All time</option>
          <option value="24h">Last 24 hours</option>
          <option value="7d">Last 7 days</option>
          <option value="30d">Last 30 days</option>
        </select>
        {loading ? (
          <span className="muted" role="status">Loading…</span>
        ) : error ? (
          <span className="error">{error}</span>
        ) : (
          <span className="muted">
            Totals across the selected window: <strong>{fmtUsd(totals.cost)}</strong> /{' '}
            <strong>{fmtNum(totals.tokens)}</strong> tokens /{' '}
            <strong>{fmtNum(totals.steps)}</strong> agent steps
          </span>
        )}
      </div>

      <section className="panel">
        <h3>By workflow</h3>
        {byWorkflow.length === 0 ? (
          <p className="muted">No agentic step executions in this window.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Workflow</th>
                <th className="num">Cost (USD)</th>
                <th className="num">Tokens</th>
                <th className="num">Steps</th>
              </tr>
            </thead>
            <tbody>
              {byWorkflow.map((r) => (
                <tr key={r.workflow_id}>
                  <td>
                    <code>{r.workflow_id}</code>
                  </td>
                  <td className="num">{fmtUsd(r.total_cost_usd)}</td>
                  <td className="num">{fmtNum(r.total_tokens)}</td>
                  <td className="num">{fmtNum(r.step_count)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="panel">
        <h3>By model</h3>
        {byModel.length === 0 ? (
          <p className="muted">No agentic step executions in this window.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Model</th>
                <th className="num">Cost (USD)</th>
                <th className="num">Tokens</th>
                <th className="num">Steps</th>
              </tr>
            </thead>
            <tbody>
              {byModel.map((r) => (
                <tr key={r.model}>
                  <td>
                    <code>{r.model}</code>
                  </td>
                  <td className="num">{fmtUsd(r.total_cost_usd)}</td>
                  <td className="num">{fmtNum(r.total_tokens)}</td>
                  <td className="num">{fmtNum(r.step_count)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="panel">
        <h3>By day</h3>
        {byDay.length === 0 ? (
          <p className="muted">No agentic step executions in this window.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th className="num">Cost (USD)</th>
                <th className="num">Tokens</th>
                <th className="num">Steps</th>
              </tr>
            </thead>
            <tbody>
              {byDay.map((r) => (
                <tr key={r.date}>
                  <td>{r.date}</td>
                  <td className="num">{fmtUsd(r.total_cost_usd)}</td>
                  <td className="num">{fmtNum(r.total_tokens)}</td>
                  <td className="num">{fmtNum(r.step_count)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
