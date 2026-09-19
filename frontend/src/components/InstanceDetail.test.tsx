import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { InstanceDetail } from './InstanceDetail';
import { api } from '../api/client';
import type { StepExecution, WorkflowInstance } from '../types';

/**
 * A dry run is a probe, and re-driving one would execute it FOR REAL — the
 * sandbox lived in the request that made it, not in the record. The backend
 * refuses resume / retry / fork on one (400, 2026-09-19). This component
 * knew nothing about dry runs, so it offered all three anyway and the user
 * met an error instead of an explanation: the same fix-one-end pattern the
 * backend change was itself correcting.
 */

function inst(over: Partial<WorkflowInstance>): WorkflowInstance {
  return {
    id: 'aaaaaaaa-0000-0000-0000-000000000000',
    workflow_id: 'wf-1',
    state: 'failed',
    trigger_payload: {},
    context: {},
    error: 'boom',
    created_at: '2026-09-19T12:00:00Z',
    started_at: '2026-09-19T12:00:00Z',
    completed_at: '2026-09-19T12:00:05Z',
    ...over,
  } as WorkflowInstance;
}

function step(over: Partial<StepExecution> = {}): StepExecution {
  return {
    id: 'bbbbbbbb-0000-0000-0000-000000000000',
    instance_id: 'aaaaaaaa-0000-0000-0000-000000000000',
    step_id: 'a',
    attempt: 1,
    state: 'completed',
    output: {},
    error: null,
    started_at: '2026-09-19T12:00:00Z',
    completed_at: '2026-09-19T12:00:01Z',
    ...over,
  } as StepExecution;
}

function mount(instance: WorkflowInstance) {
  vi.spyOn(api, 'getInstance').mockResolvedValue({
    instance,
    steps: [step()],
    raw_included: false,
  } as Awaited<ReturnType<typeof api.getInstance>>);
  vi.spyOn(api, 'instanceAudit').mockResolvedValue([]);
  vi.spyOn(api, 'listInstances').mockResolvedValue([]);
  return render(
    <MemoryRouter initialEntries={[`/instances/${instance.id}`]}>
      <Routes>
        <Route path="/instances/:id" element={<InstanceDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('InstanceDetail — a dry run is not re-drivable', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('wp.groups', 'admins');
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('offers Retry on a real failed run', async () => {
    mount(inst({ state: 'failed', context: {} }));
    expect(await screen.findByRole('button', { name: /retry/i })).toBeTruthy();
  });

  it('does NOT offer Retry on a failed DRY run', async () => {
    mount(inst({ state: 'failed', context: { dry_run: true } }));
    await screen.findByText('test run');
    expect(screen.queryByRole('button', { name: /retry/i })).toBeNull();
  });

  it('does NOT offer Resume or Kill on a paused DRY run', async () => {
    mount(inst({ state: 'paused', context: { dry_run: true } }));
    await screen.findByText('test run');
    expect(screen.queryByRole('button', { name: /resume/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /^kill$/i })).toBeNull();
  });

  it('offers Resume on a real paused run', async () => {
    mount(inst({ state: 'paused', context: {} }));
    expect(await screen.findByRole('button', { name: /resume/i })).toBeTruthy();
  });

  it('disables the per-step fork button on a dry run', async () => {
    mount(inst({ state: 'completed', context: { dry_run: true } }));
    const fork = await screen.findByRole('button', { name: /fork/i });
    expect((fork as HTMLButtonElement).disabled).toBe(true);
  });

  it('leaves fork enabled on a real run', async () => {
    mount(inst({ state: 'completed', context: {} }));
    const fork = await screen.findByRole('button', { name: /fork/i });
    expect((fork as HTMLButtonElement).disabled).toBe(false);
  });

  it('says WHY the actions are absent, rather than just hiding them', async () => {
    mount(inst({ state: 'failed', context: { dry_run: true } }));
    await waitFor(() => {
      expect(screen.getByText(/can.t be resumed or retried/i)).toBeTruthy();
    });
  });

  it('badges the run as a test so the missing actions make sense', async () => {
    mount(inst({ state: 'failed', context: { dry_run: true } }));
    expect(await screen.findByText('test run')).toBeTruthy();
  });

  it('does not badge a real run', async () => {
    mount(inst({ state: 'failed', context: {} }));
    await screen.findByRole('button', { name: /retry/i });
    expect(screen.queryByText('test run')).toBeNull();
  });
});
