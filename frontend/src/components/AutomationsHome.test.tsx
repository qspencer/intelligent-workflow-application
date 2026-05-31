import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AutomationsHome } from './AutomationsHome';
import { api } from '../api/client';
import type { WorkflowDefinition, WorkflowInstance } from '../types';

function def(over: Partial<WorkflowDefinition>): WorkflowDefinition {
  return { id: 'wf1', name: 'Flow One', description: 'A nice flow', ...over } as WorkflowDefinition;
}
function inst(over: Partial<WorkflowInstance>): WorkflowInstance {
  return { id: 'i1', workflow_id: 'wf1', state: 'completed', ...over } as WorkflowInstance;
}

describe('AutomationsHome', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('wp.groups', 'designers');
    vi.spyOn(api, 'workflowInstanceCounts').mockResolvedValue({ wf1: 3 });
    vi.spyOn(api, 'listInstances').mockResolvedValue([inst({})]);
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('renders a card per workflow with run count + latest status', async () => {
    vi.spyOn(api, 'listWorkflows').mockResolvedValue([
      def({ steps: [{ id: 'a' }, { id: 'b' }] as WorkflowDefinition['steps'] }),
    ]);
    render(
      <MemoryRouter>
        <AutomationsHome />
      </MemoryRouter>,
    );
    expect(await screen.findByText('Flow One')).toBeInTheDocument();
    expect(screen.getByText('2 steps')).toBeInTheDocument();
    expect(screen.getByText('3 runs')).toBeInTheDocument();
    expect(screen.getByText('Done')).toBeInTheDocument(); // friendly label for 'completed'
  });

  it('shows an empty state when there are no workflows', async () => {
    vi.spyOn(api, 'listWorkflows').mockResolvedValue([]);
    render(
      <MemoryRouter>
        <AutomationsHome />
      </MemoryRouter>,
    );
    expect(await screen.findByText('No automations yet.')).toBeInTheDocument();
  });

  it('creates a workflow from the dialog with the given name', async () => {
    vi.spyOn(api, 'listWorkflows').mockResolvedValue([]);
    const create = vi
      .spyOn(api, 'createWorkflow')
      .mockResolvedValue(def({ id: 'invoice-triage', name: 'Invoice triage' }));
    render(
      <MemoryRouter>
        <AutomationsHome />
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Create' }));
    fireEvent.change(screen.getByPlaceholderText('e.g. Invoice triage'), {
      target: { value: 'Invoice triage' },
    });
    // Header "Create" + dialog "Create" both exist now; submit via the dialog's.
    const submit = within(screen.getByText('Create automation').closest('.dialog') as HTMLElement);
    fireEvent.click(submit.getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(create).toHaveBeenCalledWith({ name: 'Invoice triage' }));
  });

  it('hides Create for non-designer roles', async () => {
    localStorage.setItem('wp.groups', 'viewers');
    vi.spyOn(api, 'listWorkflows').mockResolvedValue([]);
    render(
      <MemoryRouter>
        <AutomationsHome />
      </MemoryRouter>,
    );
    await screen.findByText('No automations yet.');
    expect(screen.queryByRole('button', { name: 'Create' })).not.toBeInTheDocument();
  });
});
