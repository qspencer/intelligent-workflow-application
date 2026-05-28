import { describe, expect, it } from 'vitest';

import { emptyLike, fieldKind, removeAt, setIn } from './run-form';

describe('fieldKind', () => {
  it('classifies values', () => {
    expect(fieldKind('x')).toBe('string');
    expect(fieldKind(3)).toBe('number');
    expect(fieldKind(true)).toBe('boolean');
    expect(fieldKind([])).toBe('array');
    expect(fieldKind({})).toBe('object');
    expect(fieldKind(null)).toBe('null');
    expect(fieldKind(undefined)).toBe('null');
  });
});

describe('setIn', () => {
  it('sets a top-level key immutably', () => {
    const a = { x: 1 };
    const b = setIn(a, ['x'], 2) as Record<string, number>;
    expect(b.x).toBe(2);
    expect(a.x).toBe(1); // original untouched
  });

  it('sets a nested object path', () => {
    const a = { from: { address: 'a@x.com', name: 'A' } };
    const b = setIn(a, ['from', 'name'], 'B') as typeof a;
    expect(b.from.name).toBe('B');
    expect(b.from.address).toBe('a@x.com');
    expect(a.from.name).toBe('A');
  });

  it('sets an array element by index', () => {
    const a = { to: [{ address: 'x' }, { address: 'y' }] };
    const b = setIn(a, ['to', 1, 'address'], 'z') as typeof a;
    expect(b.to[1].address).toBe('z');
    expect(b.to[0].address).toBe('x');
  });

  it('creates intermediate containers when missing', () => {
    const b = setIn({}, ['a', 'b'], 5) as Record<string, Record<string, number>>;
    expect(b.a.b).toBe(5);
  });
});

describe('removeAt', () => {
  it('splices an array element', () => {
    const a = { to: ['x', 'y', 'z'] };
    const b = removeAt(a, ['to', 1]) as typeof a;
    expect(b.to).toEqual(['x', 'z']);
    expect(a.to).toEqual(['x', 'y', 'z']);
  });

  it('deletes an object key', () => {
    const a = { x: 1, y: 2 };
    const b = removeAt(a, ['y']) as Record<string, number>;
    expect(b).toEqual({ x: 1 });
  });

  it('removes a nested array element', () => {
    const a = { wrap: { items: [1, 2, 3] } };
    const b = removeAt(a, ['wrap', 'items', 0]) as typeof a;
    expect(b.wrap.items).toEqual([2, 3]);
  });
});

describe('emptyLike', () => {
  it('produces empty values matching the template type', () => {
    expect(emptyLike('hello')).toBe('');
    expect(emptyLike(42)).toBe(0);
    expect(emptyLike(true)).toBe(false);
    expect(emptyLike([1, 2])).toEqual([]);
    expect(emptyLike({ address: 'a', n: 3, ok: true })).toEqual({
      address: '',
      n: 0,
      ok: false,
    });
  });
});
