import { Link } from 'react-router-dom';

import { fmtShort } from '../../lib/format';
import type { StepExecution, WorkflowInstance } from '../../types';

/** Bottom status bar shown when the canvas is following an instance. */
export function CanvasFooter({
  instance,
  steps,
}: {
  instance: WorkflowInstance;
  steps: StepExecution[];
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
      {instance.started_at && (
        <span className="cf-time">started {fmtShort(instance.started_at)}</span>
      )}
      <Link className="cf-link" to={`/instances/${instance.id}`}>
        Open full instance view →
      </Link>
    </div>
  );
}
