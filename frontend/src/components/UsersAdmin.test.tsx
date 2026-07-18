import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

import { api } from '../api/client';
import { resetMe } from '../lib/me';
import { UsersAdmin } from './UsersAdmin';
import type { Organization, PlatformUser } from '../types';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  resetMe();
});

function org(id: string, name?: string): Organization {
  return { id, name: name ?? id, created_at: '2026-07-18T00:00:00Z' };
}

function mockAdmin(orgs: Organization[]): void {
  vi.spyOn(api, 'listOrganizations').mockResolvedValue(orgs);
  vi.spyOn(api, 'me').mockResolvedValue({
    auth_mode: 'dev',
    identity: { sub: 'root', email: null, roles: ['Administrator'] },
    user: null,
    organization: null,
  });
}

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
    mockAdmin([org('default')]);
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
    mockAdmin([org('default')]);
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
        roles: ['Organization Viewer'],
        display_name: undefined,
      }),
    );
  });

  it('deactivation goes through updateUser', async () => {
    mockAdmin([org('default')]);
    vi.spyOn(api, 'listUsers').mockResolvedValue([user({})]);
    const update = vi.spyOn(api, 'updateUser').mockResolvedValue(user({ is_active: false }));
    render(<UsersAdmin />);
    fireEvent.click(await screen.findByRole('button', { name: 'Disable' }));
    await waitFor(() => expect(update).toHaveBeenCalledWith('u1', { is_active: false }));
  });

  it('shows the org column and an org picker when multiple orgs exist', async () => {
    mockAdmin([org('default'), org('acme', 'Acme Inc')]);
    vi.spyOn(api, 'listUsers').mockResolvedValue([user({ org_id: 'acme' })]);
    render(<UsersAdmin />);
    expect(await screen.findByText('Acme Inc')).toBeInTheDocument(); // column shows org name
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    expect(screen.getByLabelText('Organization')).toBeInTheDocument();
  });

  it('creates an organization through the Organizations dialog', async () => {
    mockAdmin([org('default')]);
    vi.spyOn(api, 'listUsers').mockResolvedValue([]);
    const create = vi.spyOn(api, 'createOrganization').mockResolvedValue(org('acme'));
    render(<UsersAdmin />);
    fireEvent.click(await screen.findByRole('button', { name: 'Organizations' }));
    fireEvent.change(screen.getByLabelText('New organization'), {
      target: { value: 'Acme' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(create).toHaveBeenCalledWith('Acme'));
  });

  it('hides org affordances for non-Administrators', async () => {
    vi.spyOn(api, 'listOrganizations').mockRejectedValue(new Error('403'));
    vi.spyOn(api, 'me').mockResolvedValue({
      auth_mode: 'dev',
      identity: { sub: 'oa', email: null, roles: ['Organization Administrator'] },
      user: null,
      organization: null,
    });
    vi.spyOn(api, 'listUsers').mockResolvedValue([user({})]);
    render(<UsersAdmin />);
    await screen.findByText('alice@example.com');
    expect(screen.queryByRole('button', { name: 'Organizations' })).toBeNull();
  });
});
