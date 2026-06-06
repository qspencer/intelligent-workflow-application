import { useCallback, useEffect, useState } from 'react';

import { api } from '../api/client';
import type { DevErrorsResponse } from '../types';

const POLL_MS = 7000;

/** Dev-only header affordance: polls /api/dev/errors and shows a red count of
 *  distinct backend errors. Clicking opens a panel listing them (with
 *  tracebacks) and a Clear button. Renders nothing when the endpoint is absent
 *  (not AUTH_MODE=dev) or there are no errors. */
export function ErrorBadge() {
  const [data, setData] = useState<DevErrorsResponse | null>(null);
  const [available, setAvailable] = useState(true);
  const [open, setOpen] = useState(false);

  const refresh = useCallback(async (): Promise<void> => {
    try {
      setData(await api.getDevErrors());
      setAvailable(true);
    } catch {
      // Endpoint absent (not running AUTH_MODE=dev) or unreachable — stay hidden.
      setAvailable(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), POLL_MS);
    return () => window.clearInterval(id);
  }, [refresh]);

  async function clear(): Promise<void> {
    try {
      await api.clearDevErrors();
    } catch {
      /* best-effort */
    }
    setOpen(false);
    await refresh();
  }

  const distinct = data?.distinct ?? 0;
  if (!available || distinct === 0) return null;

  return (
    <div className="error-badge">
      <button
        className="error-badge-button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-label={`${distinct} backend error${distinct === 1 ? '' : 's'} — click to view`}
        title={`${distinct} backend error${distinct === 1 ? '' : 's'} — click to view`}
      >
        ⚠ {distinct}
      </button>
      {open && (
        <>
          <div className="error-badge-backdrop" onClick={() => setOpen(false)} />
          <div className="error-badge-panel" role="dialog" aria-label="Backend errors">
            <div className="error-badge-panel-head">
              <strong>Backend errors ({distinct})</strong>
              <span>
                <button className="small" onClick={() => void clear()}>
                  Clear
                </button>
                <button className="small" onClick={() => setOpen(false)}>
                  Close
                </button>
              </span>
            </div>
            <ul className="error-list">
              {(data?.errors ?? []).map((e) => (
                <li key={e.fingerprint} className="error-item">
                  <div className="error-item-head">
                    <span className="error-logger">{e.logger}</span>
                    {e.count > 1 && <span className="error-count">×{e.count}</span>}
                    <span className="error-time">
                      {new Date(e.last_seen).toLocaleTimeString()}
                    </span>
                  </div>
                  <div className="error-message">{e.message}</div>
                  {e.traceback && (
                    <details>
                      <summary>traceback</summary>
                      <pre className="error-traceback">{e.traceback}</pre>
                    </details>
                  )}
                </li>
              ))}
            </ul>
          </div>
        </>
      )}
    </div>
  );
}
