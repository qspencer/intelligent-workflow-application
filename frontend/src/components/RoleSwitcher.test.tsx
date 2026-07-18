import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '../api/client';
import { resetMe } from '../lib/me';
import { RoleSwitcher } from './RoleSwitcher';

describe('RoleSwitcher', () => {
  beforeEach(() => {
    localStorage.clear();
    // jsdom's reload is unimplemented; stub it so onChange doesn't throw.
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { reload: vi.fn() },
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    resetMe();
  });

  function selectEl(): HTMLSelectElement {
    return screen.getByRole('combobox') as HTMLSelectElement;
  }

  it('defaults to admins when localStorage is empty', () => {
    render(<RoleSwitcher />);
    expect(selectEl().value).toBe('admins');
  });

  it('defaults to admins for an unknown stored role', () => {
    localStorage.setItem('wp.groups', 'orbital-marines');
    render(<RoleSwitcher />);
    expect(selectEl().value).toBe('admins');
  });

  it('reads a known role from localStorage', () => {
    localStorage.setItem('wp.groups', 'org-viewers');
    render(<RoleSwitcher />);
    expect(selectEl().value).toBe('org-viewers');
  });

  it('onChange writes localStorage, sets default user, and reloads', () => {
    render(<RoleSwitcher />);
    fireEvent.change(selectEl(), { target: { value: 'org-users' } });
    expect(localStorage.getItem('wp.groups')).toBe('org-users');
    expect(localStorage.getItem('wp.user')).toBe('dev-user');
    expect(window.location.reload).toHaveBeenCalledTimes(1);
  });

  it('preserves a pre-existing username', () => {
    localStorage.setItem('wp.user', 'alice');
    render(<RoleSwitcher />);
    fireEvent.change(selectEl(), { target: { value: 'org-admins' } });
    expect(localStorage.getItem('wp.user')).toBe('alice');
    expect(localStorage.getItem('wp.groups')).toBe('org-admins');
  });

  it('hides itself when the backend reports a non-dev auth mode', async () => {
    vi.spyOn(api, 'me').mockResolvedValue({
      auth_mode: 'local',
      identity: { sub: 'u1', email: null, roles: ['Admin'] },
      user: null,
      organization: null,
    });
    const { container } = render(<RoleSwitcher />);
    await vi.waitFor(() => expect(container).toBeEmptyDOMElement());
  });
});
