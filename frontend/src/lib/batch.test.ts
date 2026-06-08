import { describe, expect, it } from 'vitest';

import { parseBatchInput } from './batch';

describe('parseBatchInput (C8.1)', () => {
  it('parses a JSON array of objects', () => {
    expect(parseBatchInput('[{"a":1},{"a":2}]')).toEqual([{ a: 1 }, { a: 2 }]);
  });

  it('rejects non-array JSON', () => {
    expect(() => parseBatchInput('{"a":1}')).toThrow(/array/i);
  });

  it('rejects a JSON array with a non-object item', () => {
    expect(() => parseBatchInput('[{"a":1}, 5]')).toThrow(/object/i);
  });

  it('rejects an empty JSON array', () => {
    expect(() => parseBatchInput('[]')).toThrow(/empty/i);
  });

  it('parses CSV with a header row into objects', () => {
    const csv = 'name,amount\nAcme,99\nBeta,12';
    expect(parseBatchInput(csv)).toEqual([
      { name: 'Acme', amount: '99' },
      { name: 'Beta', amount: '12' },
    ]);
  });

  it('honors quoted CSV fields with commas and escaped quotes', () => {
    const csv = 'name,note\n"Acme, Inc.","he said ""hi"""';
    expect(parseBatchInput(csv)).toEqual([{ name: 'Acme, Inc.', note: 'he said "hi"' }]);
  });

  it('rejects CSV with only a header row', () => {
    expect(() => parseBatchInput('name,amount')).toThrow(/data row/i);
  });

  it('rejects blank input', () => {
    expect(() => parseBatchInput('   ')).toThrow(/No data/i);
  });
});
