import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { App } from './App';
import { api } from '../api/client';

describe('App routing + IA', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.spyOn(api, 'listInstances').mockResolvedValue([]);
    vi.spyOn(api, 'workflowInstanceCounts').mockResolvedValue({});
    vi.spyOn(api, 'listWorkflows').mockResolvedValue([]);
    vi.spyOn(api, 'listTemplates').mockResolvedValue([]);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('lands on the Automations home at the index route', async () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );
    expect(await screen.findByRole('heading', { name: 'Your automations' })).toBeInTheDocument();
  });

  it('shows the friendly nav and hides the developer console by default', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );
    expect(screen.getByRole('link', { name: 'Automations' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Templates' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Instances' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Workflows' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Cost' })).not.toBeInTheDocument();
  });

  it('reveals the developer console when Advanced is toggled on', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole('button', { name: /Developer:/ }));
    expect(screen.getByRole('link', { name: 'Instances' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Workflows' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Cost' })).toBeInTheDocument();
  });

  it('renders the workflows route directly (developer surface still routable)', async () => {
    render(
      <MemoryRouter initialEntries={['/workflows']}>
        <App />
      </MemoryRouter>,
    );
    expect(await screen.findByRole('heading', { name: 'Workflows' })).toBeInTheDocument();
  });

  it('renders the cost route', async () => {
    vi.spyOn(api, 'costByWorkflow').mockResolvedValue([]);
    vi.spyOn(api, 'costByModel').mockResolvedValue([]);
    vi.spyOn(api, 'costByDay').mockResolvedValue([]);
    render(
      <MemoryRouter initialEntries={['/cost']}>
        <App />
      </MemoryRouter>,
    );
    expect(await screen.findByRole('heading', { name: 'Cost Dashboard' })).toBeInTheDocument();
  });
});
