import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { RunDialog } from './RunDialog';
import { api } from '../../api/client';
import type { CostEstimate, WorkflowDefinition } from '../../types';

const HAIKU = 'us.anthropic.claude-haiku-4-5-20251001-v1:0';

function def(): WorkflowDefinition {
  return {
    id: 'wf1',
    name: 'WF',
    description: '',
    trigger: { type: 'manual', example_payload: {} },
  } as WorkflowDefinition;
}
function estimate(over: Partial<CostEstimate> = {}): CostEstimate {
  return {
    workflow_id: 'wf1',
    models: [{ step_id: 'classify', model: HAIKU, input_per_million: 1, output_per_million: 5 }],
    run_count: 0,
    avg_cost_usd: null,
    avg_tokens: null,
    max_total_tokens: null,
    budget_action: 'pause',
    ...over,
  };
}

describe('RunDialog cost estimate (C6.2)', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('wp.groups', 'admins');
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('shows average $/run + budget when history exists', async () => {
    vi.spyOn(api, 'getCostEstimate').mockResolvedValue(
      estimate({ run_count: 12, avg_cost_usd: 0.0034, avg_tokens: 2000, max_total_tokens: 5000 }),
    );
    render(
      <MemoryRouter>
        <RunDialog def={def()} onClose={() => {}} />
      </MemoryRouter>,
    );
    expect(await screen.findByText('$0.0034')).toBeInTheDocument();
    expect(screen.getByText(/avg of 12 runs/)).toBeInTheDocument();
    expect(screen.getByText(/budget 5,000 tok · pause at cap/)).toBeInTheDocument();
  });

  it('shows "no cost history" when the workflow has not run', async () => {
    vi.spyOn(api, 'getCostEstimate').mockResolvedValue(estimate());
    render(
      <MemoryRouter>
        <RunDialog def={def()} onClose={() => {}} />
      </MemoryRouter>,
    );
    expect(await screen.findByText(/No cost history yet — 1 AI step/)).toBeInTheDocument();
  });

  it('still renders the dialog if the estimate fails to load', async () => {
    vi.spyOn(api, 'getCostEstimate').mockRejectedValue(new Error('404'));
    render(
      <MemoryRouter>
        <RunDialog def={def()} onClose={() => {}} />
      </MemoryRouter>,
    );
    expect(await screen.findByRole('button', { name: 'Run' })).toBeInTheDocument();
    await waitFor(() => expect(api.getCostEstimate).toHaveBeenCalled());
    expect(screen.queryByText(/per run|No cost history|avg of/)).not.toBeInTheDocument();
  });
});
