import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { OutputCard } from './OutputCard';
import type { StepExecution } from '../../types';

function step(output: Record<string, unknown> | null): StepExecution {
  return {
    id: 'exec-1',
    instance_id: 'i-1',
    step_id: 'triage',
    state: 'completed',
    output,
    error: null,
    started_at: null,
    completed_at: '2026-05-28T10:00:00Z',
  };
}

afterEach(cleanup);

describe('OutputCard', () => {
  it('renders a triage card (headline, summary, chips, flags)', () => {
    render(
      <OutputCard
        step={step({
          category: 'bugfix',
          complexity: 'moderate',
          summary: 'Fixes a null deref',
          concerns: ['no test coverage'],
          needs_tests: true,
        })}
      />,
    );
    expect(screen.getByText('bugfix')).toBeInTheDocument();
    expect(screen.getByText('Fixes a null deref')).toBeInTheDocument();
    expect(screen.getByText('no test coverage')).toBeInTheDocument();
    expect(screen.getByText(/needs tests/)).toBeInTheDocument();
  });

  it('renders an eval card for evaluation-shaped output', () => {
    render(
      <OutputCard
        step={step({ parse_ok: true, faithfulness_score: 5, category_score: 4, reasoning: 'ok' })}
      />,
    );
    expect(screen.getByText('Faithfulness')).toBeInTheDocument();
    expect(screen.getByText('5 / 5')).toBeInTheDocument();
  });

  it('respects output_renderer=raw_json (forces JSON over the auto card)', () => {
    render(<OutputCard step={step({ category: 'bugfix' })} renderer="raw_json" />);
    expect(screen.getByText('Raw output')).toBeInTheDocument();
    expect(screen.queryByText('bugfix')).not.toBeInTheDocument();
  });

  it('shows status + a no-output message for empty deterministic output', () => {
    render(<OutputCard step={step({})} />);
    expect(screen.getByText('Done')).toBeInTheDocument();
    expect(screen.getByText('No output recorded.')).toBeInTheDocument();
  });
});
