import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it } from 'vitest';

import { CanvasFooter } from './CanvasFooter';
import type { StepExecution, WorkflowInstance, WorkflowPolicy } from '../../types';

function inst(): WorkflowInstance {
  return {
    id: 'inst-12345678',
    workflow_id: 'wf1',
    state: 'running',
    trigger_payload: {},
    error: null,
    created_at: '',
    started_at: null,
    completed_at: null,
  };
}
function agStep(totalTokens: number, cost: number): StepExecution {
  const half = totalTokens / 2;
  return {
    id: 's',
    instance_id: 'i',
    step_id: 'classify',
    state: 'completed',
    output: {
      usage: { input_tokens: half, output_tokens: half, total_tokens: totalTokens },
      cost_usd: cost,
      model: 'm',
    },
    error: null,
    started_at: null,
    completed_at: null,
  };
}

function renderFooter(steps: StepExecution[], policy: WorkflowPolicy | null) {
  return render(
    <MemoryRouter>
      <CanvasFooter instance={inst()} steps={steps} policy={policy} />
    </MemoryRouter>,
  );
}

describe('CanvasFooter budget meter (C6.2)', () => {
  afterEach(cleanup);

  it('shows tokens/cap and the budget action once over the warn threshold', () => {
    renderFooter([agStep(900, 0.01)], { max_total_tokens: 1000, budget_action: 'pause' });
    expect(screen.getByText(/900 \/ 1,000 tok/)).toBeInTheDocument();
    expect(screen.getByText(/pause at cap/)).toBeInTheDocument(); // 90% >= 80%
  });

  it('shows tokens + $ without a cap', () => {
    renderFooter([agStep(500, 0.02)], null);
    expect(screen.getByText(/500 tok · \$0.0200/)).toBeInTheDocument();
  });

  it('renders no meter when there is no usage and no cap', () => {
    renderFooter([], null);
    expect(screen.queryByText(/tok/)).not.toBeInTheDocument();
  });
});
