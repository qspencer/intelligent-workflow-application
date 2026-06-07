import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ValidationPanel } from './ValidationPanel';
import type { ValidationFinding } from '../../types';

function f(over: Partial<ValidationFinding> = {}): ValidationFinding {
  return {
    level: 'error',
    code: 'empty_goal',
    message: 'AI step has no goal',
    node_id: 'classify',
    edge: null,
    ...over,
  };
}

describe('ValidationPanel (C7.3)', () => {
  afterEach(cleanup);

  it('renders nothing when there are no findings', () => {
    const { container } = render(<ValidationPanel findings={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('summarises error + warning counts and lists messages', () => {
    render(
      <ValidationPanel
        findings={[
          f(),
          f({ level: 'warning', code: 'disconnected_step', message: 'x not connected', node_id: 'x' }),
        ]}
      />,
    );
    expect(screen.getByText('1 error')).toBeInTheDocument();
    expect(screen.getByText('1 warning')).toBeInTheDocument();
    expect(screen.getByText('AI step has no goal')).toBeInTheDocument();
  });

  it('clicking a node finding selects that node', () => {
    const onSelect = vi.fn();
    render(<ValidationPanel findings={[f()]} onSelect={onSelect} />);
    fireEvent.click(screen.getByRole('button', { name: 'classify' }));
    expect(onSelect).toHaveBeenCalledWith('classify');
  });
});
