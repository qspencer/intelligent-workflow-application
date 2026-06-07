import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ToolPicker } from './ToolPicker';
import type { CatalogTool } from '../../types';

const TOOLS: CatalogTool[] = [
  { name: 'file_read', description: 'Read a file', category: 'filesystem' },
  { name: 'email_send', description: 'Send an email', category: 'email' },
];

describe('ToolPicker (C7.2)', () => {
  afterEach(cleanup);

  it('lists tools grouped by category with descriptions', () => {
    render(<ToolPicker tools={TOOLS} selected={[]} onChange={() => {}} />);
    expect(screen.getByText('file_read')).toBeInTheDocument();
    expect(screen.getByText('Send an email')).toBeInTheDocument();
    expect(screen.getByText('filesystem')).toBeInTheDocument(); // group legend
  });

  it('toggles a tool on', () => {
    const onChange = vi.fn();
    render(<ToolPicker tools={TOOLS} selected={[]} onChange={onChange} />);
    fireEvent.click(screen.getByRole('checkbox', { name: /file_read/ }));
    expect(onChange).toHaveBeenCalledWith(['file_read']);
  });

  it('toggles a tool off', () => {
    const onChange = vi.fn();
    render(<ToolPicker tools={TOOLS} selected={['file_read', 'email_send']} onChange={onChange} />);
    fireEvent.click(screen.getByRole('checkbox', { name: /file_read/ }));
    expect(onChange).toHaveBeenCalledWith(['email_send']);
  });

  it('surfaces enabled tools that are not in the catalog', () => {
    render(<ToolPicker tools={TOOLS} selected={['mystery_tool']} onChange={() => {}} />);
    expect(screen.getByText(/not in this engine/i)).toBeInTheDocument();
    expect(screen.getByText(/mystery_tool/)).toBeInTheDocument();
  });
});
