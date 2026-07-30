import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { MemoryPage } from './MemoryPage';
import { api } from '../api/client';

describe('MemoryPage', () => {
  beforeEach(() => {
    vi.spyOn(api, 'memorySummary').mockResolvedValue({
      namespaces: [{ org_id: 'default', account: 'q@x.com', edges: 40, episodes: 12 }],
      unrecognized_ids: 3,
    });
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('renders namespaces with counts and the unrecognized-ids note', async () => {
    render(<MemoryPage />);
    expect(await screen.findByText('q@x.com')).toBeInTheDocument();
    expect(screen.getByText('40')).toBeInTheDocument();
    expect(screen.getByText(/3 store id\(s\)/)).toBeInTheDocument();
  });

  it('renders attacker-authored fact text inert (text nodes only)', async () => {
    vi.spyOn(api, 'memoryIntrospect').mockResolvedValue({
      facts: { 'org:X': ['<script>alert(1)</script> unverified claim'] },
      truncated: false,
    });
    render(<MemoryPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Show facts' }));
    const raw = await screen.findByText(/unverified claim/);
    // The script tag must appear as literal TEXT, not execute or vanish.
    expect(raw.textContent).toContain('<script>');
    expect(document.querySelector('script[src], .memory-raw script')).toBeNull();
  });

  it('surfaces the truncation flag', async () => {
    vi.spyOn(api, 'memoryIntrospect').mockResolvedValue({ relations: {}, truncated: true });
    render(<MemoryPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Show facts' }));
    expect(await screen.findByText(/exceeded the response cap/)).toBeInTheDocument();
  });

  it('degrades on summary failure', async () => {
    vi.spyOn(api, 'memorySummary').mockRejectedValue(new Error('boom'));
    render(<MemoryPage />);
    expect(await screen.findByText(/boom|Failed to load memory summary/)).toBeInTheDocument();
  });
});
