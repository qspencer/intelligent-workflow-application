import { isAgentic, modelDisplayName, type CanvasNodeData } from '../../lib/canvas';
import type { CapabilityReasonCode, CapabilityReportStep, StepExecution } from '../../types';
import { ExplainPanel } from './ExplainPanel';
import { OutputCard } from './OutputCard';

const CAP_TAG: Record<CapabilityReasonCode, string> = {
  capability_blocked: 'blocked',
  not_enabled: 'off',
  unknown_tool: 'missing',
};

function ConfigList({ config }: { config: Record<string, unknown> }) {
  const entries = Object.entries(config);
  if (entries.length === 0) return <p className="muted">No configuration.</p>;
  return (
    <dl className="kv">
      {entries.map(([k, v]) => (
        <div className="kv-row" key={k}>
          <dt>{k}</dt>
          <dd>{typeof v === 'string' ? v : JSON.stringify(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Inspector({
  data,
  execution,
  capability,
}: {
  data: CanvasNodeData | null;
  execution?: StepExecution | null;
  capability?: CapabilityReportStep | null;
}) {
  if (!data) {
    return (
      <aside className="canvas-inspector">
        <p className="muted">
          {execution === undefined
            ? 'Select a step to see what it does.'
            : 'Select a step to see its result.'}
        </p>
      </aside>
    );
  }

  return (
    <aside className="canvas-inspector">
      <h3>
        <span aria-hidden="true">{data.icon}</span> {data.title}
      </h3>

      {execution && (
        <div className="field">
          <span className="field-label">This run</span>
          <OutputCard step={execution} renderer={data.step?.output_renderer} />
          <ExplainPanel instanceId={execution.instance_id} stepId={execution.step_id} />
        </div>
      )}

      {data.kind === 'trigger' && data.trigger && (
        <>
          <div className="field">
            <span className="field-label">Trigger type</span>
            <span className="field-value">{data.trigger.type}</span>
          </div>
          <div className="field">
            <span className="field-label">Configuration</span>
            <ConfigList config={data.trigger.config ?? {}} />
          </div>
        </>
      )}

      {data.step && data.kind === 'deterministic' && !isAgentic(data.step) && (
        <>
          <div className="field">
            <span className="field-label">Runs the function</span>
            <span className="field-value">
              <code>{data.step.function}</code>
            </span>
          </div>
          <div className="field">
            <span className="field-label">Inputs / configuration</span>
            <ConfigList config={data.step.config} />
          </div>
        </>
      )}

      {data.step && isAgentic(data.step) && (
        <>
          <div className="field">
            <span className="field-label">Model</span>
            <span className="field-value">{modelDisplayName(data.step.model)}</span>
          </div>
          <div className="field">
            <span className="field-label">Instructions (what the AI is asked to do)</span>
            <pre className="goal">{data.step.goal.trim()}</pre>
          </div>
          <div className="field">
            <span className="field-label">Tools the AI can use</span>
            {data.step.tools.length > 0 ? (
              <ul className="tools">
                {data.step.tools.map((t) => (
                  <li key={t}>
                    <code>{t}</code>
                  </li>
                ))}
              </ul>
            ) : (
              <span className="muted">None — reasoning only.</span>
            )}
          </div>
          {capability && (
            <div className="field">
              <span className="field-label">Capability boundary</span>
              <p className="cap-summary">
                <span aria-hidden="true">🛡</span> Can use{' '}
                <strong>{capability.allowed.length}</strong> of{' '}
                {capability.allowed.length + capability.denied.length} tools
              </p>
              {capability.denied.length > 0 && (
                <ul className="tools cap-denied">
                  {capability.denied.map((d) => (
                    <li key={d.tool} className={`cap-${d.reason_code}`} title={d.reason}>
                      <code>{d.tool}</code> <span className="cap-tag">{CAP_TAG[d.reason_code]}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
          <div className="field">
            <span className="field-label">Limits</span>
            <span className="field-value">
              up to {data.step.policy.max_iterations} steps ·{' '}
              {data.step.policy.max_total_tokens.toLocaleString()} tokens max
            </span>
          </div>
        </>
      )}
    </aside>
  );
}
