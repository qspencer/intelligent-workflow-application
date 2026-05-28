import { isAgentic, modelDisplayName, type CanvasNodeData } from '../../lib/canvas';

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

export function Inspector({ data }: { data: CanvasNodeData | null }) {
  if (!data) {
    return (
      <aside className="canvas-inspector">
        <p className="muted">Select a step to see what it does.</p>
      </aside>
    );
  }

  return (
    <aside className="canvas-inspector">
      <h3>
        <span aria-hidden="true">{data.icon}</span> {data.title}
      </h3>

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
