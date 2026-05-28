import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { App } from './App';
import { api } from '../api/client';

describe('App routing', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.spyOn(api, 'listInstances').mockResolvedValue([]);
    vi.spyOn(api, 'workflowInstanceCounts').mockResolvedValue({});
    vi.spyOn(api, 'listWorkflows').mockResolvedValue([]);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('redirects the index route to the instances list', async () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );
    expect(await screen.findByRole('heading', { name: 'Workflow Instances' })).toBeInTheDocument();
  });

  it('renders the three top-level nav links', () => {
    render(
      <MemoryRouter initialEntries={['/instances']}>
        <App />
      </MemoryRouter>,
    );
    expect(screen.getByRole('link', { name: 'Instances' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Workflows' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Cost' })).toBeInTheDocument();
  });

  it('navigates to the Workflows route on nav click', async () => {
    render(
      <MemoryRouter initialEntries={['/instances']}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole('link', { name: 'Workflows' }));
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
