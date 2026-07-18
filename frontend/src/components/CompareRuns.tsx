import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import { api, errorMessage } from '../api/client';
import { compareRuns, type CompareRow, type StepFacet } from '../lib/compare';
import { categoryClass } from '../lib/memory';
import { fmtMedium } from '../lib/format';
import { formatUsage } from '../lib/usage';
import type { StepExecution, WorkflowInstance } from '../types';

function short(id: string): string {
  return id.slice(0, 8);
}
function shortHash(hash: string): string {
  return hash.replace(/^sha256:/, '').slice(0, 8);
}

interface Loaded {
  instance: WorkflowInstance;
  steps: StepExecution[];
}

function FacetCell({ facet, highlight }: { facet: StepFacet | null; highlight: boolean }) {
  if (!facet) return <td className="muted">not run</td>;
  return (
    <td className={highlight ? 'diff' : undefined}>
      <div className="facet-line">
        <span className={`badge ${facet.state}`}>{facet.state}</span>
        {facet.category && (
          <span className={`badge category ${categoryClass(facet.category)}`}>
            {facet.category}
          </span>
        )}
      </div>
      <div className="facet-line facet-tech">
        {facet.memoryHash && (
          <code className="mh" title={facet.memoryHash}>
            {shortHash(facet.memoryHash)}
          </code>
        )}
        {facet.recall && (
          <code className="recall" title={`recalled for ${facet.recall.query}`}>
            {facet.recall.edges}e·{facet.recall.episodes}ep
          </code>
        )}
        {facet.usage && <code className="usage">{formatUsage(facet.usage)}</code>}
      </div>
      {facet.signal && <div className="facet-signal">{facet.signal}</div>}
    </td>
  );
}

function RunHeader({ label, run }: { label: string; run: Loaded }) {
  const ctx = run.instance.context ?? {};
  const tokens = typeof ctx['total_tokens'] === 'number' ? ctx['total_tokens'] : null;
  const cost = typeof ctx['total_cost_usd'] === 'number' ? ctx['total_cost_usd'] : null;
  return (
    <th>
      <div>
        {label}:{' '}
        <Link to={`/instances/${run.instance.id}`}>
          <code>{short(run.instance.id)}</code>
        </Link>{' '}
        <span className={`badge ${run.instance.state}`}>{run.instance.state}</span>
      </div>
      <div className="muted run-meta">
        {fmtMedium(run.instance.started_at ?? run.instance.created_at)}
        {tokens != null && ` · ${tokens} tok`}
        {cost != null && ` · $${cost.toFixed(4)}`}
      </div>
    </th>
  );
}

/** G5: side-by-side comparison of two runs of the same workflow, grouped by
 * step, with the memory hash (rubric version) and recall each step saw —
 * built to answer "why did this run behave differently from that one?" */
export function CompareRuns() {
  const { a = '', b = '' } = useParams<{ a: string; b: string }>();
  const [runA, setRunA] = useState<Loaded | null>(null);
  const [runB, setRunB] = useState<Loaded | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let ignore = false;
    Promise.all([api.getInstance(a), api.getInstance(b)])
      .then(([da, db]) => {
        if (ignore) return;
        setRunA(da);
        setRunB(db);
        setError(null);
      })
      .catch((err) => {
        if (!ignore) setError(errorMessage(err, 'Failed to load runs'));
      });
    return () => {
      ignore = true;
    };
  }, [a, b]);

  if (error) return <p className="error">{error}</p>;
  if (!runA || !runB) return <p>Loading…</p>;

  const rows: CompareRow[] = compareRuns(runA.steps, runB.steps);
  const crossWorkflow = runA.instance.workflow_id !== runB.instance.workflow_id;
  const hashesDiffer = rows.some((r) => r.hashDiffers);

  return (
    <div className="page-compare">
      <p>
        <Link to={`/instances/${a}`}>← Back to instance</Link>
      </p>
      <h2>Compare runs — {runA.instance.workflow_id}</h2>
      {crossWorkflow && (
        <p className="error">
          These runs belong to different workflows ({runA.instance.workflow_id} vs{' '}
          {runB.instance.workflow_id}) — the comparison may not be meaningful.
        </p>
      )}
      {!hashesDiffer && (
        <p className="muted">
          Both runs saw the same rubric version{rows.length > 0 ? '' : 's'} — differences
          below come from inputs or recalled memory, not rubric edits.
        </p>
      )}
      <table className="compare">
        <thead>
          <tr>
            <th>Step</th>
            <RunHeader label="A" run={runA} />
            <RunHeader label="B" run={runB} />
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.stepId}>
              <td>
                {r.stepId}
                {(r.hashDiffers || r.categoryDiffers) && (
                  <div className="diff-flags">
                    {r.hashDiffers && <span className="diff-flag">rubric changed</span>}
                    {r.categoryDiffers && <span className="diff-flag">verdict changed</span>}
                  </div>
                )}
              </td>
              <FacetCell facet={r.a} highlight={r.hashDiffers || r.categoryDiffers} />
              <FacetCell facet={r.b} highlight={r.hashDiffers || r.categoryDiffers} />
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
