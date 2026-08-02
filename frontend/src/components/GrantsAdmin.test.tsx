import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

import { api } from '../api/client';
import { GrantsAdmin } from './GrantsAdmin';
import type { AuditEntry, PlatformUser, RawTraceGrant } from '../types';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function grant(overrides: Partial<RawTraceGrant>): RawTraceGrant {
  return {
    id: 'g1',
    principal_id: 'u1',
    org_id: 'default',
    state: 'active',
    approval_mode: 'dual_administrator',
    requested_by: 'admin1',
    approved_by: 'admin1',
    external_approval_ref: null,
    expires_at: null,
    reason_code: 'debugging',
    ticket_ref: null,
    revoked_by: null,
    revoked_at: null,
    ...overrides,
  };
}

const bob: PlatformUser = {
  id: 'u1',
  iss: 'dev',
  sub: 'bob',
  email: 'bob@example.com',
  display_name: null,
  org_id: 'default',
  roles: ['Organization User'],
  is_active: true,
  has_password: false,
  created_at: '2026-08-02T00:00:00Z',
  last_seen_at: '2026-08-02T00:00:00Z',
};

function mock(grants: RawTraceGrant[], log: AuditEntry[] = []): void {
  vi.spyOn(api, 'listRawTraceGrants').mockResolvedValue(grants);
  vi.spyOn(api, 'listUsers').mockResolvedValue([bob]);
  vi.spyOn(api, 'listOrganizations').mockResolvedValue([
    { id: 'default', name: 'default', created_at: '2026-08-02T00:00:00Z' },
  ]);
  vi.spyOn(api, 'listRecentAudit').mockResolvedValue(log);
}

describe('GrantsAdmin', () => {
  it('renders a grant with the principal email and a state badge', async () => {
    mock([grant({ state: 'active' })]);
    render(<GrantsAdmin />);
    expect(await screen.findByText('bob@example.com')).toBeTruthy();
    expect(screen.getByText('active')).toBeTruthy();
  });

  it('offers Approve on a pending grant and calls the API', async () => {
    mock([grant({ state: 'pending', approved_by: null })]);
    const approve = vi.spyOn(api, 'approveRawTraceGrant').mockResolvedValue(grant({ state: 'active' }));
    render(<GrantsAdmin />);
    const btn = await screen.findByRole('button', { name: 'Approve' });
    fireEvent.click(btn);
    await waitFor(() => expect(approve).toHaveBeenCalledWith('g1'));
  });

  it('surfaces the raw-access log from raw_trace_* audit entries', async () => {
    const evt: AuditEntry = {
      id: 'a1',
      timestamp: '2026-08-02T12:00:00Z',
      actor_type: 'human',
      actor_id: 'carol',
      action: 'raw_trace_release_decided',
      workflow_instance_id: 'i1',
      step_id: null,
      detail: { surface: 'detail', outcome: 'released' },
    };
    mock([grant({})], [evt]);
    render(<GrantsAdmin />);
    expect(await screen.findByText('raw_trace_release_decided')).toBeTruthy();
    expect(screen.getByText(/surface=detail/)).toBeTruthy();
  });
});
