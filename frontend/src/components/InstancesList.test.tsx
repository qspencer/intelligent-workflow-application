import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { InstancesList } from './InstancesList';
import { api } from '../api/client';
import type { WorkflowInstance } from '../types';

function inst(over: Partial<WorkflowInstance>): WorkflowInstance {
  return {
    id: 'aaaaaaaa-0000-0000-0000-000000000000',
    workflow_id: 'wf-1',
    state: 'completed',
    trigger_payload: {},
    error: null,
    created_at: '2026-07-12T12:00:00Z',
    started_at: '2026-07-12T12:00:00Z',
    completed_at: '2026-07-12T12:00:05Z',
    ...over,
  } as WorkflowInstance;
}

describe('InstancesList bulk clear (delete-safety)', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('wp.groups', 'admins');
    vi.spyOn(window, 'confirm').mockReturnValue(true);
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('scopes the bulk delete to the active workflow filter', async () => {
    vi.spyOn(api, 'listInstances').mockResolvedValue([inst({})]);
    const del = vi
      .spyOn(api, 'deleteInstancesByStates')
      .mockResolvedValue({ deleted_instances: 1, deleted_steps: 1 });
    render(
      <MemoryRouter initialEntries={['/instances?workflow_id=wf-1']}>
        <InstancesList />
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByRole('button', { name: /Delete Finished \(1\)/ }));
    await waitFor(() =>
      expect(del).toHaveBeenCalledWith(['completed', 'failed', 'killed'], 'wf-1'),
    );
    // Scoped confirm names the workflow.
    expect(vi.mocked(window.confirm).mock.calls[0][0]).toContain('"wf-1"');
  });

  it('unfiltered: labels the button as all-workflows and warns platform-wide', async () => {
    vi.spyOn(api, 'listInstances').mockResolvedValue([inst({})]);
    const del = vi
      .spyOn(api, 'deleteInstancesByStates')
      .mockResolvedValue({ deleted_instances: 200, deleted_steps: 400 });
    render(
      <MemoryRouter initialEntries={['/instances']}>
        <InstancesList />
      </MemoryRouter>,
    );
    fireEvent.click(
      await screen.findByRole('button', { name: /Delete Finished — all workflows/ }),
    );
    await waitFor(() =>
      expect(del).toHaveBeenCalledWith(['completed', 'failed', 'killed'], undefined),
    );
    const msg = vi.mocked(window.confirm).mock.calls[0][0] as string;
    expect(msg).toContain('EVERY workflow');
    expect(msg).toContain('platform-wide');
  });

  it('shows the Result column (category + subject) from run context', async () => {
    vi.spyOn(api, 'listInstances').mockResolvedValue([
      inst({
        trigger_payload: { subject: 'Order Confirmation #123' },
        context: { steps: { record: { parse_ok: true, category: 'fyi' } } },
      }),
    ]);
    render(
      <MemoryRouter initialEntries={['/instances']}>
        <InstancesList />
      </MemoryRouter>,
    );
    expect(await screen.findByText('fyi')).toBeInTheDocument();
    expect(screen.getByText('Order Confirmation #123')).toBeInTheDocument();
  });
});

describe('InstancesList — a dry run is not re-drivable', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('wp.groups', 'admins');
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  async function rows(over: Partial<WorkflowInstance>) {
    vi.spyOn(api, 'listInstances').mockResolvedValue([inst(over)]);
    render(
      <MemoryRouter initialEntries={['/instances']}>
        <InstancesList />
      </MemoryRouter>,
    );
    await waitFor(() => expect(api.listInstances).toHaveBeenCalled());
  }

  it('offers Retry on a real failed run', async () => {
    await rows({ state: 'failed' });
    expect(await screen.findByRole('button', { name: /retry/i })).toBeTruthy();
  });

  it('does NOT offer Retry on a failed dry run', async () => {
    await rows({ state: 'failed', dry_run: true });
    await screen.findByText('test run');
    expect(screen.queryByRole('button', { name: /retry/i })).toBeNull();
  });

  it('does NOT offer Resume on a paused dry run', async () => {
    await rows({ state: 'paused', dry_run: true });
    await screen.findByText('test run');
    expect(screen.queryByRole('button', { name: /resume/i })).toBeNull();
  });

  it('offers Resume on a real paused run', async () => {
    await rows({ state: 'paused' });
    expect(await screen.findByRole('button', { name: /resume/i })).toBeTruthy();
  });

  it('still lets you delete a dry run', async () => {
    await rows({ state: 'failed', dry_run: true });
    // The page also carries a bulk-clear control, so scope to the row's own
    // Delete by its title rather than by the shared accessible name.
    expect(
      await screen.findByTitle(/delete this test run/i),
    ).toBeTruthy();
  });
});
