import { statusMeta } from '../../lib/canvas';
import { extractEvaluations, scoreClass } from '../../lib/evaluation';
import { fmtShort } from '../../lib/format';
import { extractUsage, formatUsage } from '../../lib/usage';
import type { StepExecution } from '../../types';

/**
 * Read-only "what this step did on this run" card, shown in the Inspector
 * when the canvas is following an instance. Eval-aware (score badges for
 * `record_evaluation`-shaped output), otherwise shows the agent's text or a
 * collapsible JSON fallback.
 */
export function OutputCard({ step }: { step: StepExecution }) {
  const meta = statusMeta(step.state);
  const usage = extractUsage(step);
  const evaluation = extractEvaluations([step])[0];
  const output = step.output;
  const outputText =
    output && typeof output['output_text'] === 'string'
      ? (output['output_text'] as string)
      : null;

  return (
    <div className="output-card">
      <div className="oc-head">
        <span className={`node-status ${meta.cssClass}`}>
          <span aria-hidden="true">{meta.icon}</span> {meta.label}
        </span>
        {step.completed_at && <span className="oc-time">{fmtShort(step.completed_at)}</span>}
        {usage && <code className="oc-usage">{formatUsage(usage)}</code>}
      </div>

      {step.error && <div className="error oc-error">{step.error}</div>}

      {evaluation && evaluation.parse_ok ? (
        <div className="oc-eval">
          <div className="scores">
            {evaluation.faithfulness_score !== undefined && (
              <div className="score">
                <span className="label">Faithfulness</span>
                <span className={`value ${scoreClass(evaluation.faithfulness_score)}`}>
                  {evaluation.faithfulness_score} / 5
                </span>
              </div>
            )}
            {evaluation.category_score !== undefined && (
              <div className="score">
                <span className="label">Category</span>
                <span className={`value ${scoreClass(evaluation.category_score)}`}>
                  {evaluation.category_score} / 5
                </span>
              </div>
            )}
          </div>
          {evaluation.reasoning && <div className="reasoning">{evaluation.reasoning}</div>}
          {evaluation.issues && evaluation.issues.length > 0 && (
            <ul className="issues">
              {evaluation.issues.map((issue) => (
                <li key={issue}>{issue}</li>
              ))}
            </ul>
          )}
        </div>
      ) : outputText ? (
        <pre className="oc-text">{outputText}</pre>
      ) : output && Object.keys(output).length > 0 ? (
        <details className="oc-raw">
          <summary>Raw output</summary>
          <pre>{JSON.stringify(output, null, 2)}</pre>
        </details>
      ) : (
        <p className="muted">No output recorded.</p>
      )}
    </div>
  );
}
