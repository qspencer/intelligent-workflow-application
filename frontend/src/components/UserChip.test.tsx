import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';

import { api } from '../api/client';
import { resetMe } from '../lib/me';
import { UserChip } from './UserChip';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  resetMe();
});

describe('UserChip', () => {
  it('shows the persisted user and org from /api/me', async () => {
    vi.spyOn(api, 'me').mockResolvedValue({
      auth_mode: 'dev',
      identity: { sub: 'quentin', email: null, roles: ['Admin'] },
      user: {
        id: 'u1',
        iss: 'dev',
        sub: 'quentin',
        email: null,
        display_name: null,
        org_id: 'default',
      },
      organization: { id: 'default', name: 'default' },
    });
    render(<UserChip />);
    expect(await screen.findByText('quentin')).toBeInTheDocument();
    expect(screen.getByText('@ default')).toBeInTheDocument();
  });

  it('renders nothing when /api/me fails (never breaks the shell)', async () => {
    vi.spyOn(api, 'me').mockRejectedValue(new Error('401'));
    const { container } = render(<UserChip />);
    // Give the rejected promise a tick to settle.
    await new Promise((r) => setTimeout(r, 0));
    expect(container).toBeEmptyDOMElement();
  });
});
