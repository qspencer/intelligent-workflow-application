import { beforeEach, describe, expect, it } from 'vitest';

import { advancedEnabled, setAdvanced } from './advanced';

describe('advanced mode toggle', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('defaults to off', () => {
    expect(advancedEnabled()).toBe(false);
  });

  it('persists on/off through localStorage', () => {
    setAdvanced(true);
    expect(advancedEnabled()).toBe(true);
    expect(localStorage.getItem('wp.advanced')).toBe('1');
    setAdvanced(false);
    expect(advancedEnabled()).toBe(false);
    expect(localStorage.getItem('wp.advanced')).toBeNull();
  });
});
