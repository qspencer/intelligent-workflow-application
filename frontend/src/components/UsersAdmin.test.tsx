import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

import { api } from '../api/client';
import { UsersAdmin } from './UsersAdmin';
import type { PlatformUser } from '../types';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function user(overrides: Partial<PlatformUser>): PlatformUser {
  return {
    id: 'u1',
    iss: 'local',
    sub: 'u1',
    email: 'alice@example.com',
    display_name: null,
    org_id: 'default',
    roles: ['Admin'],
    is_active: true,
    has_password: true,
    created_at: '2026-07-18T00:00:00Z',
    last_seen_at: '2026-07-18T00:00:00Z',
    ...overrides,
  };
}

describe('UsersAdmin', () => {
  it('lists users; SSO rows get no edit affordance', async () => {
    vi.spyOn(api, 'listUsers').mockResolvedValue([
      user({}),
      user({ id: 'u2', sub: 'quentin', iss: 'dev', email: 'q@y.z', has_password: false }),
    ]);
    render(<UsersAdmin />);
    expect(await screen.findByText('alice@example.com')).toBeInTheDocument();
    expect(screen.getByText('q@y.z')).toBeInTheDocument();
    // One local row → exactly one Edit button.
    expect(screen.getAllByRole('button', { name: 'Edit' })).toHaveLength(1);
  });

  it('creates a user through the dialog', async () => {
    vi.spyOn(api, 'listUsers').mockResolvedValue([]);
    const create = vi.spyOn(api, 'createUser').mockResolvedValue(user({}));
    render(<UsersAdmin />);
    fireEvent.click(await screen.findByRole('button', { name: 'Add user' }));
    fireEvent.change(screen.getByLabelText('Email'), {
      target: { value: 'new@example.com' },
    });
    fireEvent.change(screen.getByLabelText('Password (min 8 characters)'), {
      target: { value: 'longenough' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    await waitFor(() =>
      expect(create).toHaveBeenCalledWith({
        email: 'new@example.com',
        password: 'longenough',
        roles: ['Viewer'],
        display_name: undefined,
      }),
    );
  });

  it('deactivation goes through updateUser', async () => {
    vi.spyOn(api, 'listUsers').mockResolvedValue([user({})]);
    const update = vi.spyOn(api, 'updateUser').mockResolvedValue(user({ is_active: false }));
    render(<UsersAdmin />);
    fireEvent.click(await screen.findByRole('button', { name: 'Disable' }));
    await waitFor(() => expect(update).toHaveBeenCalledWith('u1', { is_active: false }));
  });
});
