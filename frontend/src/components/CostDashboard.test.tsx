import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { CostDashboard } from './CostDashboard';
import { api } from '../api/client';

describe('CostDashboard', () => {
  beforeEach(() => {
    vi.spyOn(api, 'costByWorkflow').mockResolvedValue([
      { workflow_id: 'wf-1', total_cost_usd: 0.5, total_tokens: 1000, step_count: 2 },
      { workflow_id: 'wf-2', total_cost_usd: 0.25, total_tokens: 500, step_count: 1 },
    ]);
    vi.spyOn(api, 'costByModel').mockResolvedValue([]);
    vi.spyOn(api, 'costByDay').mockResolvedValue([]);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('renders the by-workflow rows', async () => {
    render(<CostDashboard />);
    expect(await screen.findByText('wf-1')).toBeInTheDocument();
    expect(screen.getByText('wf-2')).toBeInTheDocument();
  });

  it('sums totals across the by-workflow rows', async () => {
    render(<CostDashboard />);
    // 0.5 + 0.25 = 0.75 → $0.7500; 1500 tokens; 3 steps.
    expect(await screen.findByText('$0.7500')).toBeInTheDocument();
    expect(screen.getByText('1,500')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
  });

  it('shows the empty message for sections with no rows', async () => {
    render(<CostDashboard />);
    await screen.findByText('wf-1');
    // by-model and by-day are empty → two "No agentic..." messages.
    expect(
      screen.getAllByText('No agentic step executions in this window.'),
    ).toHaveLength(2);
  });
});
