import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

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

  afterEach(cleanup);

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
    localStorage.setItem('wp.groups', 'viewers');
    render(<RoleSwitcher />);
    expect(selectEl().value).toBe('viewers');
  });

  it('onChange writes localStorage, sets default user, and reloads', () => {
    render(<RoleSwitcher />);
    fireEvent.change(selectEl(), { target: { value: 'operators' } });
    expect(localStorage.getItem('wp.groups')).toBe('operators');
    expect(localStorage.getItem('wp.user')).toBe('dev-user');
    expect(window.location.reload).toHaveBeenCalledTimes(1);
  });

  it('preserves a pre-existing username', () => {
    localStorage.setItem('wp.user', 'alice');
    render(<RoleSwitcher />);
    fireEvent.change(selectEl(), { target: { value: 'auditors' } });
    expect(localStorage.getItem('wp.user')).toBe('alice');
    expect(localStorage.getItem('wp.groups')).toBe('auditors');
  });
});
