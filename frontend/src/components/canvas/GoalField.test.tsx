import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { GoalField } from './GoalField';

describe('GoalField (C7.4)', () => {
  afterEach(cleanup);

  it('reframes the goal as AI instructions with inline help', () => {
    render(<GoalField value="do the thing" onChange={() => {}} />);
    expect(screen.getByText('Instructions for the AI')).toBeInTheDocument();
    expect(screen.getByText(/what the AI reads as its task/i)).toBeInTheDocument();
    expect(screen.getByLabelText('AI instructions')).toHaveValue('do the thing');
  });

  it('edits the value', () => {
    const onChange = vi.fn();
    render(<GoalField value="" onChange={onChange} />);
    fireEvent.change(screen.getByLabelText('AI instructions'), { target: { value: 'classify it' } });
    expect(onChange).toHaveBeenCalledWith('classify it');
  });

  it('toggles examples on and off', () => {
    render(<GoalField value="" onChange={() => {}} />);
    expect(screen.queryByText(/Classify the document/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'See examples' }));
    expect(screen.getByText(/Classify the document/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Hide examples' }));
    expect(screen.queryByText(/Classify the document/i)).not.toBeInTheDocument();
  });
});
