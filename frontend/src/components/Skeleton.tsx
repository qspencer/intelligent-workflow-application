/** Loading skeletons (C8 continuous polish).
 *
 * One placeholder per page shape so a loading view previews its layout
 * instead of collapsing to "Loading…" text. The container is a polite
 * live region (WCAG 4.1.3) with visually-hidden text for screen readers;
 * the shimmer bars themselves are decorative. The shimmer animation is
 * gated behind `prefers-reduced-motion: no-preference` in styles.css.
 */

interface SkeletonProps {
  variant?: 'table' | 'cards' | 'detail';
  /** Table rows or cards to sketch. */
  count?: number;
}

function Bar({ width, height }: { width: string; height?: string }) {
  return <div className="skeleton-bar" style={{ width, height }} />;
}

// Deterministic per-row widths so the sketch looks organic without Math.random.
const ROW_WIDTHS = ['72%', '58%', '65%', '49%', '61%', '54%'];

export function Skeleton({ variant = 'table', count = 4 }: SkeletonProps) {
  return (
    <div role="status" aria-live="polite" className="skeleton">
      <span className="visually-hidden">Loading…</span>
      <div aria-hidden="true">
        {variant === 'cards' && (
          <div className="card-grid">
            {Array.from({ length: count }, (_, i) => (
              <div key={i} className="skeleton-card">
                <Bar width="55%" height="14px" />
                <Bar width="85%" />
                <Bar width="40%" />
              </div>
            ))}
          </div>
        )}
        {variant === 'table' && (
          <div className="skeleton-table">
            <Bar width="100%" height="34px" />
            {Array.from({ length: count }, (_, i) => (
              <Bar key={i} width={ROW_WIDTHS[i % ROW_WIDTHS.length]} />
            ))}
          </div>
        )}
        {variant === 'detail' && (
          <div className="skeleton-table">
            <Bar width="35%" height="20px" />
            <Bar width="60%" />
            <Bar width="100%" height="34px" />
            {Array.from({ length: count }, (_, i) => (
              <Bar key={i} width={ROW_WIDTHS[i % ROW_WIDTHS.length]} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
