import type { CatalogTool } from '../../types';

/** Catalog-driven tool picker (C7.2) — the "connector picker". Tools grouped by
 *  category with their descriptions, checked to enable on an agent step. Tools
 *  already enabled but absent from this engine's catalog are surfaced separately
 *  rather than silently dropped. */
export function ToolPicker({
  tools,
  selected,
  onChange,
}: {
  tools: CatalogTool[];
  selected: string[];
  onChange: (next: string[]) => void;
}) {
  const toggle = (name: string) =>
    onChange(selected.includes(name) ? selected.filter((n) => n !== name) : [...selected, name]);

  const groups = new Map<string, CatalogTool[]>();
  for (const t of tools) {
    const list = groups.get(t.category) ?? [];
    list.push(t);
    groups.set(t.category, list);
  }
  const known = new Set(tools.map((t) => t.name));
  const extras = selected.filter((n) => !known.has(n));

  if (tools.length === 0) {
    return <p className="muted">No tools available in this engine's catalog.</p>;
  }

  return (
    <div className="tool-picker">
      {[...groups.entries()].map(([category, items]) => (
        <fieldset key={category} className="tp-group">
          <legend>{category}</legend>
          {items.map((t) => (
            <label key={t.name} className="tp-item" title={t.description}>
              <input
                type="checkbox"
                checked={selected.includes(t.name)}
                onChange={() => toggle(t.name)}
              />
              <span className="tp-name">{t.name}</span>
              <span className="tp-desc">{t.description}</span>
            </label>
          ))}
        </fieldset>
      ))}
      {extras.length > 0 && (
        <p className="tp-extra muted">
          Also enabled (not in this engine's catalog): {extras.join(', ')}
        </p>
      )}
    </div>
  );
}
