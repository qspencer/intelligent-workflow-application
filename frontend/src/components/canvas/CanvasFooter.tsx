import { Link } from 'react-router-dom';

import { fmtShort } from '../../lib/format';
import { extractUsage } from '../../lib/usage';
import type { StepExecution, WorkflowInstance, WorkflowPolicy } from '../../types';

/** Live budget meter (C6.2): tokens + $ accumulated so far across agentic steps,
 *  against the workflow's `max_total_tokens` cap when one is set. */
function BudgetMeter({
  steps,
  policy,
}: {
  steps: StepExecution[];
  policy: WorkflowPolicy | null;
}) {
  let tokens = 0;
  let cost = 0;
  for (const s of steps) {
    const u = extractUsage(s);
    if (u) {
      tokens += u.totalTokens;
      cost += u.costUsd;
    }
  }
  const cap = policy?.max_total_tokens ?? null;
  // Nothing to show until an agent has run (and no cap to show against).
  if (tokens === 0 && cap === null) return null;

  const pct = cap ? Math.min(100, Math.round((tokens / cap) * 100)) : null;
  const level = pct === null ? 'ok' : pct >= 100 ? 'err' : pct >= 80 ? 'warn' : 'ok';
  const dollars = `$${cost.toFixed(4)}`;

  if (cap === null) {
    return (
      <span className="cf-budget" title="No budget cap set for this workflow">
        {tokens.toLocaleString()} tok · {dollars}
      </span>
    );
  }
  return (
    <span
      className={`cf-budget cf-budget-${level}`}
      title={`${tokens.toLocaleString()} / ${cap.toLocaleString()} tokens (${pct}%) · ${dollars}`}
    >
      <span className="cf-meter">
        <span className="cf-meter-fill" style={{ width: `${pct}%` }} />
      </span>
      {tokens.toLocaleString()} / {cap.toLocaleString()} tok · {dollars}
      {policy && pct !== null && pct >= 80 ? ` · ${policy.budget_action} at cap` : ''}
    </span>
  );
}

/** Bottom status bar shown when the canvas is following an instance. */
export function CanvasFooter({
  instance,
  steps,
  policy = null,
}: {
  instance: WorkflowInstance;
  steps: StepExecution[];
  policy?: WorkflowPolicy | null;
}) {
  const done = steps.filter((s) =>
    ['completed', 'skipped', 'failed'].includes(s.state),
  ).length;
  const live = instance.state === 'running' || instance.state === 'pending';

  return (
    <div className="canvas-footer">
      <span className={`badge ${instance.state}`}>{instance.state}</span>
      <code className="cf-id">{instance.id.slice(0, 8)}</code>
      {steps.length > 0 && (
        <span className="cf-progress">
          {done} of {steps.length} steps{live ? '…' : ''}
        </span>
      )}
      <BudgetMeter steps={steps} policy={policy} />
      {instance.started_at && (
        <span className="cf-time">started {fmtShort(instance.started_at)}</span>
      )}
      <Link className="cf-link" to={`/instances/${instance.id}`}>
        Open full instance view →
      </Link>
    </div>
  );
}
