import { beforeEach, describe, expect, it, vi } from 'vitest';

import { RoleSwitcherComponent } from './role-switcher.component';

describe('RoleSwitcherComponent', () => {
  beforeEach(() => {
    localStorage.clear();
    // jsdom's reload is unimplemented; stub it so onChange() doesn't throw.
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { reload: vi.fn() },
    });
  });

  it('defaults to admins when localStorage is empty', () => {
    const c = new RoleSwitcherComponent();
    expect(c.currentGroups()).toBe('admins');
  });

  it('defaults to admins when localStorage has an unknown role string', () => {
    localStorage.setItem('wp.groups', 'orbital-marines');
    const c = new RoleSwitcherComponent();
    expect(c.currentGroups()).toBe('admins');
  });

  it('reads the current role from localStorage', () => {
    localStorage.setItem('wp.groups', 'viewers');
    const c = new RoleSwitcherComponent();
    expect(c.currentGroups()).toBe('viewers');
  });

  it('onChange writes localStorage + sets default user + reloads', () => {
    const c = new RoleSwitcherComponent();
    const event = {
      target: { value: 'operators' } as HTMLSelectElement,
    } as unknown as Event;
    c.onChange(event);

    expect(localStorage.getItem('wp.groups')).toBe('operators');
    expect(localStorage.getItem('wp.user')).toBe('dev-user');
    expect(window.location.reload).toHaveBeenCalledTimes(1);
    expect(c.currentGroups()).toBe('operators');
  });

  it('onChange preserves a pre-existing username', () => {
    localStorage.setItem('wp.user', 'alice');
    const c = new RoleSwitcherComponent();
    const event = { target: { value: 'auditors' } } as unknown as Event;
    c.onChange(event);
    expect(localStorage.getItem('wp.user')).toBe('alice');
    expect(localStorage.getItem('wp.groups')).toBe('auditors');
  });
});
