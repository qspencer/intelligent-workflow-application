import { emptyLike, fieldKind, type Path } from '../../lib/run-form';

export type Update = (path: Path, value: unknown) => void;
export type Remove = (path: Path) => void;

function ScalarInput({
  kind,
  value,
  onChange,
}: {
  kind: 'string' | 'number' | 'boolean' | 'null';
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  if (kind === 'boolean') {
    return (
      <input
        type="checkbox"
        checked={value === true}
        onChange={(e) => onChange(e.target.checked)}
      />
    );
  }
  if (kind === 'number') {
    return (
      <input
        type="number"
        value={typeof value === 'number' ? value : ''}
        onChange={(e) => onChange(e.target.value === '' ? 0 : Number(e.target.value))}
      />
    );
  }
  const str = value === null || value === undefined ? '' : String(value);
  if (str.length > 60 || str.includes('\n')) {
    return <textarea rows={3} value={str} onChange={(e) => onChange(e.target.value)} />;
  }
  return <input type="text" value={str} onChange={(e) => onChange(e.target.value)} />;
}

/**
 * Recursive editor for an arbitrary JSON-ish value (string/number/boolean/
 * object/array). Shared by the Run dialog (build a trigger payload) and the
 * canvas edit Inspector (edit a step's config). Mutations are path-based and
 * immutable — see `lib/run-form`.
 */
export function FieldEditor({
  label,
  value,
  path,
  update,
  remove,
}: {
  label: string;
  value: unknown;
  path: Path;
  update: Update;
  remove: Remove;
}) {
  const kind = fieldKind(value);

  if (kind === 'object') {
    return (
      <fieldset className="rf-object">
        {label && <legend>{label}</legend>}
        {Object.entries(value as Record<string, unknown>).map(([k, v]) => (
          <FieldEditor
            key={k}
            label={k}
            value={v}
            path={[...path, k]}
            update={update}
            remove={remove}
          />
        ))}
      </fieldset>
    );
  }

  if (kind === 'array') {
    const arr = value as unknown[];
    return (
      <div className="rf-array">
        <div className="rf-label">{label}</div>
        {arr.map((item, i) => (
          <div className="rf-array-item" key={i}>
            <FieldEditor
              label={`#${i + 1}`}
              value={item}
              path={[...path, i]}
              update={update}
              remove={remove}
            />
            <button type="button" className="link" onClick={() => remove([...path, i])}>
              remove
            </button>
          </div>
        ))}
        <button
          type="button"
          className="link"
          onClick={() => update(path, [...arr, emptyLike(arr[0] ?? '')])}
        >
          + Add another
        </button>
      </div>
    );
  }

  const scalarKind = kind as 'string' | 'number' | 'boolean' | 'null';
  return (
    <label className={`rf-field${scalarKind === 'boolean' ? ' rf-bool' : ''}`}>
      <span className="rf-label">{label}</span>
      <ScalarInput kind={scalarKind} value={value} onChange={(v) => update(path, v)} />
    </label>
  );
}
