import { useEffect, useState } from 'react';

import { api, errorMessage } from '../api/client';
import type { MemoryNamespace, MemorySummary } from '../types';
import { Skeleton } from './Skeleton';

/** Memory transparency surface (VERACIUM_041_ADOPTION_PLAN §2c): what has
 *  the system learned, about whom, from whose evidence? Facts render as
 *  TEXT NODES ONLY — they contain attacker-authored mail content; no
 *  dangerouslySetInnerHTML, no markdown, anywhere in this tree. */
export function MemoryPage() {
  const [summary, setSummary] = useState<MemorySummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<MemoryNamespace | null>(null);
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [showFacts, setShowFacts] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);

  useEffect(() => {
    let ignore = false;
    api
      .memorySummary()
      .then((data) => {
        if (!ignore) setSummary(data);
      })
      .catch((err) => {
        if (!ignore) setError(errorMessage(err, 'Failed to load memory summary'));
      });
    return () => {
      ignore = true;
    };
  }, []);

  async function open(ns: MemoryNamespace, mode: 'summary' | 'categories'): Promise<void> {
    setSelected(ns);
    setShowFacts(mode === 'categories');
    setLoadingDetail(true);
    setDetailError(null);
    try {
      setDetail(await api.memoryIntrospect(ns.org_id, ns.account, mode));
    } catch (err) {
      setDetail(null);
      setDetailError(errorMessage(err, 'Failed to introspect namespace'));
    } finally {
      setLoadingDetail(false);
    }
  }

  return (
    <div className="page-memory">
      <div className="header">
        <h2>Memory</h2>
        <p className="muted">
          What the platform has learned, per mailbox — counts by relation, evidence author, and
          disclosure tier, with the facts themselves on demand.
        </p>
      </div>
      {error ? (
        <p className="error">{error}</p>
      ) : summary === null ? (
        <Skeleton count={3} />
      ) : summary.namespaces.length === 0 ? (
        <p className="muted">No learned memory yet.</p>
      ) : (
        <>
          <table>
            <thead>
              <tr>
                <th>Account</th>
                <th>Org</th>
                <th className="num-col">Facts</th>
                <th className="num-col">Episodes</th>
                <th className="actions-col">Inspect</th>
              </tr>
            </thead>
            <tbody>
              {summary.namespaces.map((ns) => (
                <tr key={`${ns.org_id}:${ns.account}`}>
                  <td>
                    <code>{ns.account}</code>
                  </td>
                  <td>{ns.org_id}</td>
                  <td className="num-col">{ns.edges}</td>
                  <td className="num-col">{ns.episodes}</td>
                  <td className="actions-col">
                    <button onClick={() => void open(ns, 'summary')}>Summary</button>
                    <button onClick={() => void open(ns, 'categories')}>Show facts</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {summary.unrecognized_ids != null && summary.unrecognized_ids > 0 && (
            <p className="muted">
              {summary.unrecognized_ids} store id(s) don't match the org namespace shape and are
              not shown (legacy keys).
            </p>
          )}
        </>
      )}

      {selected && (
        <div className="memory-detail">
          <h3>
            {showFacts ? 'Facts' : 'Summary'} — <code>{selected.account}</code>{' '}
            <span className="muted">({selected.org_id})</span>
          </h3>
          {loadingDetail ? (
            <Skeleton count={2} />
          ) : detailError ? (
            <p className="error">{detailError}</p>
          ) : detail ? (
            <>
              {detail['truncated'] === true && (
                <p className="effect-warning">
                  Facts view exceeded the response cap — showing summary counts instead.
                </p>
              )}
              <pre className="memory-raw">{JSON.stringify(detail, null, 2)}</pre>
            </>
          ) : null}
        </div>
      )}
    </div>
  );
}
