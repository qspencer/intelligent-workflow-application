import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { EditInspector } from './EditInspector';
import { TRIGGER_NODE_ID, newStep } from '../../lib/canvas';
import type { WorkflowCatalog, WorkflowDefinition } from '../../types';

const CATALOG: WorkflowCatalog = {
  triggers: [
    { type: 'manual', label: 'Manual', description: 'Run on demand.', config_fields: [] },
    {
      type: 'filesystem',
      label: 'File drop',
      description: 'Watch a folder.',
      config_fields: [{ name: 'path', required: true, description: 'Folder to watch.' }],
    },
  ],
  functions: [
    { name: 'noop', description: 'Pass through.' },
    { name: 'append_file', description: 'Append text to a file.' },
  ],
  tools: [{ name: 'file_read', description: 'Read a file', category: 'filesystem' }],
};

function draft(): WorkflowDefinition {
  return {
    id: 'wf',
    name: 'WF',
    description: '',
    trigger: { type: 'manual', config: {} },
    steps: [newStep('agentic', 'ai1'), newStep('deterministic', 'fn1')],
    edges: [],
  };
}

describe('EditInspector catalog pickers (C7.2)', () => {
  afterEach(cleanup);

  it('renders the function as a catalog select and reports the chosen function', () => {
    const onChange = vi.fn();
    render(
      <EditInspector
        draft={draft()}
        selectedId="fn1"
        onChange={onChange}
        onDeleteStep={() => {}}
        catalog={CATALOG}
      />,
    );
    // The function field is the select that carries the catalog's function options.
    const appendOption = screen.getByRole('option', { name: 'append_file' }) as HTMLOptionElement;
    const select = appendOption.closest('select') as HTMLSelectElement;
    fireEvent.change(select, { target: { value: 'append_file' } });
    const next = onChange.mock.calls[0][0] as WorkflowDefinition;
    expect(next.steps?.find((s) => s.id === 'fn1')).toMatchObject({ function: 'append_file' });
  });

  it('renders the tool picker for an agent step', () => {
    render(
      <EditInspector
        draft={draft()}
        selectedId="ai1"
        onChange={() => {}}
        onDeleteStep={() => {}}
        catalog={CATALOG}
      />,
    );
    expect(screen.getByRole('checkbox', { name: /file_read/ })).toBeInTheDocument();
  });

  it('renders the trigger type as a catalog select with config hints', () => {
    const onChange = vi.fn();
    render(
      <EditInspector
        draft={{ ...draft(), trigger: { type: 'filesystem', config: {} } }}
        selectedId={TRIGGER_NODE_ID}
        onChange={onChange}
        onDeleteStep={() => {}}
        catalog={CATALOG}
      />,
    );
    expect(screen.getByText('Watch a folder.')).toBeInTheDocument();
    expect(screen.getByText('path')).toBeInTheDocument(); // required config-field hint
  });

  it('falls back to a text input when no catalog is provided', () => {
    render(
      <EditInspector draft={draft()} selectedId="fn1" onChange={() => {}} onDeleteStep={() => {}} />,
    );
    // No catalog → function is a plain text input, so no function options exist.
    expect(screen.queryByRole('option', { name: 'append_file' })).not.toBeInTheDocument();
  });
});
