import { beforeEach, describe, expect, it } from 'vitest';

import { authHeaders, currentGroups, currentUser, hasRole } from './auth';

describe('auth', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('authHeaders uses defaults when localStorage is empty', () => {
    expect(authHeaders()).toEqual({
      'X-Dev-User': 'dev-user',
      'X-Dev-Groups': 'admins',
    });
  });

  it('authHeaders reads user + groups from localStorage', () => {
    localStorage.setItem('wp.user', 'alice');
    localStorage.setItem('wp.groups', 'auditors,viewers');
    expect(authHeaders()).toEqual({
      'X-Dev-User': 'alice',
      'X-Dev-Groups': 'auditors,viewers',
    });
  });

  it('falls back to default user but keeps custom groups', () => {
    localStorage.setItem('wp.groups', 'org-users');
    expect(currentUser()).toBe('dev-user');
    expect(currentGroups()).toBe('org-users');
  });

  it('hasRole matches the effective group (admins when unset)', () => {
    expect(hasRole(['admins'])).toBe(true);
    expect(hasRole(['org-users'])).toBe(false);
    localStorage.setItem('wp.groups', 'org-users');
    expect(hasRole(['admins', 'org-users'])).toBe(true);
    expect(hasRole(['org-viewers'])).toBe(false);
  });
});
