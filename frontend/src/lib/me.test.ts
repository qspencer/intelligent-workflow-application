import { afterEach, describe, expect, it, vi } from 'vitest';

import { api } from '../api/client';
import { getMe, resetMe } from './me';

afterEach(() => {
  vi.restoreAllMocks();
  resetMe();
});

describe('getMe', () => {
  it('memoizes: one backend call for many consumers', async () => {
    const me = vi.spyOn(api, 'me').mockResolvedValue({
      auth_mode: 'local',
      identity: { sub: 'u1', email: null, roles: ['Admin'] },
      user: null,
      organization: null,
    });
    await Promise.all([getMe(), getMe(), getMe()]);
    expect(me).toHaveBeenCalledTimes(1);
  });

  it('resolves null on failure and can be reset', async () => {
    vi.spyOn(api, 'me').mockRejectedValue(new Error('down'));
    expect(await getMe()).toBeNull();
    resetMe();
    vi.spyOn(api, 'me').mockResolvedValue({
      auth_mode: 'dev',
      identity: { sub: 'u1', email: null, roles: [] },
      user: null,
      organization: null,
    });
    expect((await getMe())?.auth_mode).toBe('dev');
  });
});
