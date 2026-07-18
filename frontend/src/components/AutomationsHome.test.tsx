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
    localStorage.setItem('wp.groups', 'org-users');
    vi.spyOn(api, 'workflowInstanceCounts').mockResolvedValue({ wf1: 3 });
    vi.spyOn(api, 'listInstances').mockResolvedValue([inst({})]);
    vi.spyOn(api, 'listTemplates').mockResolvedValue([]);
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

  it('excludes bundled-example workflows (they belong in Templates)', async () => {
    // The orchestrator registers examples as real workflows; the home must not
    // list them, or it duplicates the Templates gallery.
    vi.spyOn(api, 'listWorkflows').mockResolvedValue([
      def({ id: 'email-triage', name: 'Email Triage' }), // a bundled example
      def({ id: 'my-flow', name: 'My Flow' }), // user-created
    ]);
    vi.spyOn(api, 'listTemplates').mockResolvedValue([
      { id: 'email-triage', name: 'Email Triage', description: '', step_count: 2, trigger_type: 'manual' },
    ]);
    render(
      <MemoryRouter>
        <AutomationsHome />
      </MemoryRouter>,
    );
    expect(await screen.findByText('My Flow')).toBeInTheDocument();
    expect(screen.queryByText('Email Triage')).not.toBeInTheDocument();
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

  it('drafts a workflow from a plain-English description (C7.1)', async () => {
    vi.spyOn(api, 'listWorkflows').mockResolvedValue([]);
    const scaffold = vi.spyOn(api, 'scaffoldWorkflow').mockResolvedValue({
      status: 'created',
      workflow_id: 'drafted',
      name: 'Drafted',
      findings: [],
    });
    render(
      <MemoryRouter>
        <AutomationsHome />
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Describe it' }));
    fireEvent.change(screen.getByPlaceholderText(/When a PDF lands/i), {
      target: { value: 'do a thing' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Draft it' }));
    await waitFor(() => expect(scaffold).toHaveBeenCalledWith('do a thing'));
  });

  it('deletes a workflow from its card after confirming', async () => {
    vi.spyOn(api, 'listWorkflows').mockResolvedValue([def({ id: 'cruft', name: 'Cruft Flow' })]);
    const del = vi
      .spyOn(api, 'deleteWorkflow')
      .mockResolvedValue({ deleted_workflow: 'cruft', deleted_instances: 0, deleted_steps: 0 });
    render(
      <MemoryRouter>
        <AutomationsHome />
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Delete Cruft Flow' }));
    // Confirm in the dialog (its button is labelled just "Delete").
    const dialog = within(screen.getByText('Delete automation').closest('.dialog') as HTMLElement);
    fireEvent.click(dialog.getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(del).toHaveBeenCalledWith('cruft'));
  });

  it('hides the card delete button for non-designer roles', async () => {
    localStorage.setItem('wp.groups', 'viewers');
    vi.spyOn(api, 'listWorkflows').mockResolvedValue([def({ id: 'cruft', name: 'Cruft Flow' })]);
    render(
      <MemoryRouter>
        <AutomationsHome />
      </MemoryRouter>,
    );
    expect(await screen.findByText('Cruft Flow')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Delete Cruft Flow' })).not.toBeInTheDocument();
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
