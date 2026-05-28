import type { ReactNode } from 'react';

import { statusMeta } from '../../lib/canvas';
import { extractEvaluations, scoreClass } from '../../lib/evaluation';
import { fmtShort } from '../../lib/format';
import { extractTriage, type TriageCard } from '../../lib/triage';
import { extractUsage, formatUsage } from '../../lib/usage';
import type { StepExecution } from '../../types';

/**
 * Read-only "what this step did on this run" card, shown in the Inspector
 * when the canvas is following an instance. Picks a renderer in this order:
 * the step's `output_renderer` (if set) wins; otherwise auto-detect —
 * triage-shaped → triage card, eval-shaped → score card, else text / JSON.
 */
export function OutputCard({
  step,
  renderer,
}: {
  step: StepExecution;
  renderer?: string | null;
}) {
  const meta = statusMeta(step.state);
  const usage = extractUsage(step);
  const output = step.output;
  const evaluation = extractEvaluations([step])[0];
  const triage = extractTriage(output);
  const outputText =
    output && typeof output['output_text'] === 'string'
      ? (output['output_text'] as string)
      : null;

  const forced = renderer && renderer !== '' && renderer !== 'auto' ? renderer : null;

  function body(): ReactNode {
    if (forced === 'raw_json') return rawJson(output);
    if (forced === 'eval') return evaluation ? evalBody(evaluation) : rawJson(output);
    if (forced === 'triage') return triage ? triageBody(triage) : rawJson(output);
    // auto
    if (triage) return triageBody(triage);
    if (evaluation) return evalBody(evaluation);
    if (outputText) return <pre className="oc-text">{outputText}</pre>;
    if (output && Object.keys(output).length > 0) return rawJson(output);
    return <p className="muted">No output recorded.</p>;
  }

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
      {body()}
    </div>
  );
}

function triageBody(t: TriageCard): ReactNode {
  return (
    <div className="oc-triage">
      <div className="oc-triage-head">
        {t.headline && <span className="badge oc-headline">{t.headline}</span>}
        {t.score !== undefined && (
          <span className={`oc-score value ${scoreClass(t.score)}`}>{t.score} / 5</span>
        )}
        {t.complexity && <span className="oc-meta">complexity: {t.complexity}</span>}
        {t.confidence !== undefined && (
          <span className="oc-meta">confidence: {Math.round(t.confidence * 100)}%</span>
        )}
      </div>
      {t.summary && <div className="reasoning">{t.summary}</div>}
      {t.chips.map((c) => (
        <div className="oc-chips" key={c.label}>
          <span className="oc-chips-label">{c.label}:</span>
          {c.items.map((it) => (
            <span className="chip" key={it}>
              {it}
            </span>
          ))}
        </div>
      ))}
      {t.flags.length > 0 && (
        <div className="oc-flags">
          {t.flags.map((f) => (
            <span className={`chip ${f.value ? 'on' : 'off'}`} key={f.label}>
              {f.label}: {f.value ? 'yes' : 'no'}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function evalBody(e: ReturnType<typeof extractEvaluations>[number]): ReactNode {
  if (!e.parse_ok) {
    return e.raw ? <pre className="oc-text">{e.raw}</pre> : <p className="muted">Unparseable.</p>;
  }
  return (
    <div className="oc-eval">
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
            <span className={`value ${scoreClass(e.category_score)}`}>{e.category_score} / 5</span>
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
    </div>
  );
}

function rawJson(output: Record<string, unknown> | null): ReactNode {
  if (!output || Object.keys(output).length === 0) {
    return <p className="muted">No output recorded.</p>;
  }
  return (
    <details className="oc-raw">
      <summary>Raw output</summary>
      <pre>{JSON.stringify(output, null, 2)}</pre>
    </details>
  );
}
