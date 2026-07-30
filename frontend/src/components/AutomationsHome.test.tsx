import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
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
    vi.spyOn(api, 'workflowAttribution').mockResolvedValue({});
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

  it('shows bundled workflows with a badge instead of hiding them (IA_PLAN)', async () => {
    // The old home filtered template-id matches out — which hid the
    // PRODUCTION workloads. The merged catalog shows everything, badged.
    vi.spyOn(api, 'listWorkflows').mockResolvedValue([
      def({ id: 'email-triage', name: 'Email Triage' }), // a bundled example
      def({ id: 'my-flow', name: 'My Flow' }), // user-created
    ]);
    vi.spyOn(api, 'workflowAttribution').mockResolvedValue({
      'email-triage': {
        org_id: 'default',
        org_name: 'default',
        source: 'bundled',
        lifecycle: 'reseeded',
        run_effect: 'mutating',
        effect_tools: ['email_label_apply'],
      },
      'my-flow': { org_id: 'default', org_name: 'default', source: 'user', run_effect: 'read_only' },
    });
    render(
      <MemoryRouter>
        <AutomationsHome />
      </MemoryRouter>,
    );
    expect(await screen.findByText('My Flow')).toBeInTheDocument();
    expect(screen.getByText('Email Triage')).toBeInTheDocument();
    expect(screen.getByText('Bundled')).toBeInTheDocument();
    // Bundled rows get no delete affordance; user rows do.
    expect(screen.queryByRole('button', { name: 'Delete Email Triage' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Delete My Flow' })).toBeInTheDocument();
  });

  it('opens the Run dialog from the home; mutating workflows need explicit confirmation', async () => {
    vi.spyOn(api, 'listWorkflows').mockResolvedValue([def({ id: 'wf1', name: 'Flow One' })]);
    vi.spyOn(api, 'workflowAttribution').mockResolvedValue({
      wf1: {
        org_id: 'default',
        org_name: 'default',
        source: 'user',
        run_effect: 'mutating',
        effect_tools: ['email_send'],
      },
    });
    render(
      <MemoryRouter>
        <AutomationsHome />
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Run Flow One' }));
    expect(await screen.findByText(/acts on external systems/)).toBeInTheDocument();
    // Run stays disabled until the effect checkbox is ticked.
    const runButton = screen.getByRole('button', { name: 'Run' });
    expect(runButton).toBeDisabled();
    fireEvent.click(screen.getByLabelText(/changes external systems/));
    expect(runButton).not.toBeDisabled();
  });

  it('read-only workflows get no effect warning (no crying wolf)', async () => {
    vi.spyOn(api, 'listWorkflows').mockResolvedValue([def({ id: 'wf1', name: 'Flow One' })]);
    vi.spyOn(api, 'workflowAttribution').mockResolvedValue({
      wf1: { org_id: 'default', org_name: 'default', source: 'user', run_effect: 'read_only' },
    });
    render(
      <MemoryRouter>
        <AutomationsHome />
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Run Flow One' }));
    expect(await screen.findByRole('heading', { name: /Run/ })).toBeInTheDocument();
    expect(screen.queryByText(/acts on external systems/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Run' })).not.toBeDisabled();
  });

  it('degrades gracefully when attribution fails: no badges, delete stays, run warns', async () => {
    vi.spyOn(api, 'listWorkflows').mockResolvedValue([def({ id: 'wf1', name: 'Flow One' })]);
    vi.spyOn(api, 'workflowAttribution').mockRejectedValue(new Error('boom'));
    render(
      <MemoryRouter>
        <AutomationsHome />
      </MemoryRouter>,
    );
    expect(await screen.findByText('Flow One')).toBeInTheDocument();
    expect(screen.queryByText('Bundled')).not.toBeInTheDocument();
    // Unknown attribution counts as mutating: the run dialog must warn.
    fireEvent.click(screen.getByRole('button', { name: 'Run Flow One' }));
    expect(await screen.findByText(/acts on external systems/)).toBeInTheDocument();
  });

  it('table view renders the same workflow set as cards (one list, two renderings)', async () => {
    vi.spyOn(api, 'listWorkflows').mockResolvedValue([
      def({ id: 'wf1', name: 'Flow One' }),
      def({ id: 'wf2', name: 'Flow Two' }),
    ]);
    render(
      <MemoryRouter initialEntries={['/?view=table']}>
        <AutomationsHome />
      </MemoryRouter>,
    );
    expect(await screen.findByRole('table')).toBeInTheDocument();
    expect(screen.getByText('Flow One')).toBeInTheDocument();
    expect(screen.getByText('Flow Two')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Table' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('viewers see no write affordances in either rendering', async () => {
    localStorage.setItem('wp.groups', 'org-viewers');
    vi.spyOn(api, 'listWorkflows').mockResolvedValue([def({ id: 'wf1', name: 'Flow One' })]);
    render(
      <MemoryRouter>
        <AutomationsHome />
      </MemoryRouter>,
    );
    expect(await screen.findByText('Flow One')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Run/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Delete/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Create' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Import' })).not.toBeInTheDocument();
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
