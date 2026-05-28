/** Helpers for building a trigger payload from a form generated off
 *  `trigger.example_payload`. Pure + immutable so the React form can keep
 *  the payload in one state object and update it by path. */

export type PathSegment = string | number;
export type Path = PathSegment[];

export type FieldKind = 'string' | 'number' | 'boolean' | 'object' | 'array' | 'null';

export function fieldKind(value: unknown): FieldKind {
  if (value === null || value === undefined) return 'null';
  if (Array.isArray(value)) return 'array';
  if (typeof value === 'object') return 'object';
  if (typeof value === 'number') return 'number';
  if (typeof value === 'boolean') return 'boolean';
  return 'string';
}

/** Immutably set the value at `path`, creating intermediate containers. */
export function setIn(root: unknown, path: Path, value: unknown): unknown {
  if (path.length === 0) return value;
  const [head, ...rest] = path;
  if (typeof head === 'number') {
    const arr = Array.isArray(root) ? [...root] : [];
    arr[head] = setIn(arr[head], rest, value);
    return arr;
  }
  const obj =
    root && typeof root === 'object' && !Array.isArray(root)
      ? { ...(root as Record<string, unknown>) }
      : {};
  obj[head] = setIn(obj[head], rest, value);
  return obj;
}

/** Immutably remove the element/key at `path` (array splice or object delete). */
export function removeAt(root: unknown, path: Path): unknown {
  if (path.length === 0) return root;
  const [head, ...rest] = path;
  if (rest.length === 0) {
    if (typeof head === 'number' && Array.isArray(root)) {
      const arr = [...root];
      arr.splice(head, 1);
      return arr;
    }
    if (root && typeof root === 'object' && !Array.isArray(root)) {
      const obj = { ...(root as Record<string, unknown>) };
      delete obj[head as string];
      return obj;
    }
    return root;
  }
  if (typeof head === 'number' && Array.isArray(root)) {
    const arr = [...root];
    arr[head] = removeAt(arr[head], rest);
    return arr;
  }
  if (root && typeof root === 'object') {
    const obj = { ...(root as Record<string, unknown>) };
    obj[head as string] = removeAt(obj[head as string], rest);
    return obj;
  }
  return root;
}

/** An empty value shaped like `template` — used to seed a new array item. */
export function emptyLike(template: unknown): unknown {
  switch (fieldKind(template)) {
    case 'string':
      return '';
    case 'number':
      return 0;
    case 'boolean':
      return false;
    case 'array':
      return [];
    case 'object': {
      const out: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(template as Record<string, unknown>)) {
        out[k] = emptyLike(v);
      }
      return out;
    }
    default:
      return '';
  }
}
