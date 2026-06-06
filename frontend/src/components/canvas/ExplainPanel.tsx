import { useEffect, useState } from 'react';

import { api } from '../../api/client';
import type { ExplainStep } from '../../types';

/** Explain-this-run (C6.4): forensic view of one step in a run — for an agent,
 *  what it was asked, the tools it called (args + results), tokens/cost, and the
 *  memory hash in effect; for a deterministic step, its function + output.
 *  Best-effort — renders nothing if the endpoint is unavailable. */
export function ExplainPanel({ instanceId, stepId }: { instanceId: string; stepId: string }) {
  const [data, setData] = useState<ExplainStep | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let ignore = false;
    setData(null);
    setFailed(false);
    api
      .getExplain(instanceId, stepId)
      .then((d) => !ignore && setData(d))
      .catch(() => !ignore && setFailed(true));
    return () => {
      ignore = true;
    };
  }, [instanceId, stepId]);

  if (failed || !data) return null;

  const agentic = data.kind === 'agentic';
  return (
    <details className="explain" open>
      <summary>Explain this step</summary>

      {agentic ? (
        <>
          <p className="explain-meta">
            {data.model && <span>{data.model}</span>}
            {data.iterations != null && <span>{data.iterations} iter</span>}
            {data.usage?.['total_tokens'] != null && (
              <span>{data.usage['total_tokens']} tok</span>
            )}
            {data.cost_usd != null && <span>${data.cost_usd.toFixed(4)}</span>}
          </p>
          {data.memory_hash && (
            <p className="explain-mem" title="Agent memory version in effect for this step">
              memory <code>{data.memory_hash}</code>
            </p>
          )}
          {data.goal && (
            <div className="explain-field">
              <span className="explain-label">Asked to</span>
              <pre className="explain-pre">{data.goal}</pre>
            </div>
          )}
          <div className="explain-field">
            <span className="explain-label">
              Tool calls ({data.tool_calls?.length ?? 0})
            </span>
            {data.tool_calls && data.tool_calls.length > 0 ? (
              <ul className="explain-calls">
                {data.tool_calls.map((c, i) => (
                  <li key={i} className="explain-call">
                    <code className="explain-call-name">{c.name}</code>
                    {c.input && <pre className="explain-pre">in: {c.input}</pre>}
                    {c.result && <pre className="explain-pre">out: {c.result}</pre>}
                  </li>
                ))}
              </ul>
            ) : (
              <span className="muted">No tools called — reasoning only.</span>
            )}
          </div>
          {data.output_text && (
            <div className="explain-field">
              <span className="explain-label">Result</span>
              <pre className="explain-pre">{data.output_text}</pre>
            </div>
          )}
        </>
      ) : (
        <>
          {data.function && (
            <p className="explain-meta">
              <span>
                fn <code>{data.function}</code>
              </span>
            </p>
          )}
          {data.output && (
            <div className="explain-field">
              <span className="explain-label">Output</span>
              <pre className="explain-pre">{data.output}</pre>
            </div>
          )}
        </>
      )}

      {data.error && (
        <div className="explain-field">
          <span className="explain-label">Error</span>
          <pre className="explain-pre explain-error">{data.error}</pre>
        </div>
      )}
    </details>
  );
}
