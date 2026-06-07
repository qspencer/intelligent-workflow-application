import type { ValidationFinding } from '../../types';

/** Build-time validation findings (C7.3), shown while editing. Clicking a
 *  finding with a node selects that node on the canvas. */
export function ValidationPanel({
  findings,
  onSelect,
}: {
  findings: ValidationFinding[];
  onSelect?: (nodeId: string) => void;
}) {
  if (findings.length === 0) return null;
  const errors = findings.filter((f) => f.level === 'error').length;
  const warnings = findings.length - errors;

  return (
    <div className="validation-panel" role="status" aria-live="polite">
      <div className="validation-summary">
        {errors > 0 && (
          <span className="vp-err">
            {errors} error{errors === 1 ? '' : 's'}
          </span>
        )}
        {warnings > 0 && (
          <span className="vp-warn">
            {warnings} warning{warnings === 1 ? '' : 's'}
          </span>
        )}
      </div>
      <ul className="validation-list">
        {findings.map((f, i) => (
          <li key={i} className={`vp-item vp-${f.level}`}>
            <span className="vp-icon" aria-hidden="true">
              {f.level === 'error' ? '⛔' : '⚠'}
            </span>
            {f.node_id &&
              (onSelect ? (
                <button className="vp-node-link" onClick={() => onSelect(f.node_id as string)}>
                  {f.node_id}
                </button>
              ) : (
                <code>{f.node_id}</code>
              ))}
            <span className="vp-msg">{f.message}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
