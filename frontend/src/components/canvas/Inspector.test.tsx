import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { Inspector } from './Inspector';
import type { CanvasNodeData } from '../../lib/canvas';
import type { CapabilityReportStep, WorkflowStep } from '../../types';

function agenticData(): CanvasNodeData {
  const step = {
    id: 'classify',
    type: 'agentic',
    goal: 'classify the thing',
    tools: ['file_read'],
    model: 'us.anthropic.claude-haiku-4-5-20251001-v1:0',
    policy: { max_iterations: 5, max_total_tokens: 4000 },
    outputs: [],
  } as unknown as WorkflowStep;
  return { kind: 'agentic', title: 'Classify', subtitle: '', icon: '🤖', step };
}

function capability(): CapabilityReportStep {
  return {
    step_id: 'classify',
    model: 'haiku',
    allowed: ['file_read'],
    denied: [
      {
        tool: 'file_write',
        reason: 'Blocked by the step capability allowlist',
        reason_code: 'capability_blocked',
      },
      { tool: 'http_fetch', reason: 'Not enabled for this step', reason_code: 'not_enabled' },
    ],
  };
}

describe('Inspector capability boundary (C6.3)', () => {
  afterEach(cleanup);

  it('summarises allowed/total and lists denied tools with tags', () => {
    render(<Inspector data={agenticData()} capability={capability()} />);
    expect(screen.getByText(/Capability boundary/)).toBeInTheDocument();
    expect(screen.getByText(/Can use/).textContent).toMatch(/Can use\s+1\s+of\s+3\s+tools/);

    // capability-blocked tool is highlighted; not-enabled is muted
    const blocked = screen.getByText('file_write').closest('li');
    expect(blocked?.className).toContain('cap-capability_blocked');
    expect(screen.getByText('blocked')).toBeInTheDocument();

    const off = screen.getByText('http_fetch').closest('li');
    expect(off?.className).toContain('cap-not_enabled');
    expect(screen.getByText('off')).toBeInTheDocument();
  });

  it('omits the section when no capability report is provided', () => {
    render(<Inspector data={agenticData()} />);
    expect(screen.queryByText(/Capability boundary/)).not.toBeInTheDocument();
  });
});
