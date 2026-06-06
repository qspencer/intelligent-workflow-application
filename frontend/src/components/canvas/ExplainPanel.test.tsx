import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ExplainPanel } from './ExplainPanel';
import { api } from '../../api/client';
import type { ExplainStep } from '../../types';

function agentic(over: Partial<ExplainStep> = {}): ExplainStep {
  return {
    instance_id: 'i',
    step_id: 'classify',
    state: 'completed',
    kind: 'agentic',
    started_at: null,
    completed_at: null,
    error: null,
    model: 'haiku',
    memory_hash: 'sha256:abc123',
    stop_reason: 'end_turn',
    iterations: 2,
    usage: { total_tokens: 120 },
    cost_usd: 0.002,
    goal: 'Classify the document',
    output_text: 'It is an invoice.',
    tool_calls: [
      { name: 'file_read', input: '{"path":"/x"}', result: '{"text":"hi"}', timestamp: 't' },
    ],
    ...over,
  };
}

describe('ExplainPanel (C6.4)', () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('shows tool calls, memory hash, and meta for an agent step', async () => {
    vi.spyOn(api, 'getExplain').mockResolvedValue(agentic());
    render(<ExplainPanel instanceId="i" stepId="classify" />);
    expect(await screen.findByText('Explain this step')).toBeInTheDocument();
    expect(screen.getByText('sha256:abc123')).toBeInTheDocument();
    expect(screen.getByText('file_read')).toBeInTheDocument();
    expect(screen.getByText(/Tool calls \(1\)/)).toBeInTheDocument();
    expect(screen.getByText(/Classify the document/)).toBeInTheDocument();
  });

  it('shows function + output for a deterministic step (no tool calls)', async () => {
    vi.spyOn(api, 'getExplain').mockResolvedValue({
      instance_id: 'i',
      step_id: 'route',
      state: 'completed',
      kind: 'deterministic',
      started_at: null,
      completed_at: null,
      error: null,
      function: 'route_files',
      output: '{"copied_to":"/out"}',
    } as ExplainStep);
    render(<ExplainPanel instanceId="i" stepId="route" />);
    expect(await screen.findByText('route_files')).toBeInTheDocument();
    expect(screen.getByText(/copied_to/)).toBeInTheDocument();
    expect(screen.queryByText(/Tool calls/)).not.toBeInTheDocument();
  });

  it('renders nothing when the endpoint fails', async () => {
    vi.spyOn(api, 'getExplain').mockRejectedValue(new Error('404'));
    const { container } = render(<ExplainPanel instanceId="i" stepId="x" />);
    await waitFor(() => expect(api.getExplain).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
