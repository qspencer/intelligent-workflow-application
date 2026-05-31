import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { TemplatesGallery } from './TemplatesGallery';
import { api } from '../api/client';
import type { WorkflowDefinition, WorkflowTemplate } from '../types';

const TEMPLATES: WorkflowTemplate[] = [
  {
    id: 'email-triage',
    name: 'Email Triage',
    description: 'Sort inbound mail',
    step_count: 3,
    trigger_type: 'webhook',
  },
];

describe('TemplatesGallery', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('wp.groups', 'designers');
    vi.spyOn(api, 'listTemplates').mockResolvedValue(TEMPLATES);
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('renders a card per template with a friendly trigger label', async () => {
    render(
      <MemoryRouter>
        <TemplatesGallery />
      </MemoryRouter>,
    );
    expect(await screen.findByText('Email Triage')).toBeInTheDocument();
    expect(screen.getByText('3 steps')).toBeInTheDocument();
    expect(screen.getByText('On a webhook')).toBeInTheDocument();
  });

  it('clones a template via createWorkflow on use', async () => {
    const create = vi
      .spyOn(api, 'createWorkflow')
      .mockResolvedValue({ id: 'email-triage-copy' } as WorkflowDefinition);
    render(
      <MemoryRouter>
        <TemplatesGallery />
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Use this template' }));
    await waitFor(() =>
      expect(create).toHaveBeenCalledWith({ template_id: 'email-triage' }),
    );
  });

  it('hides the use button for non-designer roles', async () => {
    localStorage.setItem('wp.groups', 'viewers');
    render(
      <MemoryRouter>
        <TemplatesGallery />
      </MemoryRouter>,
    );
    await screen.findByText('Email Triage');
    expect(screen.queryByRole('button', { name: 'Use this template' })).not.toBeInTheDocument();
  });
});
