import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ErrorBadge } from './ErrorBadge';
import { api } from '../api/client';
import type { DevError, DevErrorsResponse } from '../types';

function err(over: Partial<DevError> = {}): DevError {
  return {
    fingerprint: 'fp1',
    level: 'ERROR',
    logger: 'workflow_platform.triggers.gmail_poll',
    message: 'Gmail poll failed',
    traceback: 'Traceback...\nValueError: boom',
    count: 1,
    first_seen: '2026-06-01T12:00:00+00:00',
    last_seen: '2026-06-01T12:00:00+00:00',
    ...over,
  };
}
function resp(over: Partial<DevErrorsResponse> = {}): DevErrorsResponse {
  return { total: 0, distinct: 0, errors: [], ...over };
}

describe('ErrorBadge', () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('renders nothing when there are no errors', async () => {
    vi.spyOn(api, 'getDevErrors').mockResolvedValue(resp());
    const { container } = render(<ErrorBadge />);
    await waitFor(() => expect(api.getDevErrors).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing when the endpoint is unavailable (not dev mode)', async () => {
    vi.spyOn(api, 'getDevErrors').mockRejectedValue(new Error('404'));
    const { container } = render(<ErrorBadge />);
    await waitFor(() => expect(api.getDevErrors).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it('shows the distinct count and opens a panel listing errors', async () => {
    vi.spyOn(api, 'getDevErrors').mockResolvedValue(
      resp({ total: 42, distinct: 1, errors: [err({ count: 42 })] }),
    );
    render(<ErrorBadge />);
    const badge = await screen.findByRole('button', { name: /backend error/i });
    expect(badge).toHaveTextContent('1'); // distinct count, not total
    fireEvent.click(badge);
    expect(screen.getByText('Gmail poll failed')).toBeInTheDocument();
    expect(screen.getByText('×42')).toBeInTheDocument(); // occurrence count
  });

  it('Clear calls the API and re-fetches', async () => {
    vi.spyOn(api, 'getDevErrors')
      .mockResolvedValueOnce(resp({ total: 1, distinct: 1, errors: [err()] }))
      .mockResolvedValue(resp()); // after clear: empty
    const clear = vi.spyOn(api, 'clearDevErrors').mockResolvedValue({ status: 'cleared' });

    render(<ErrorBadge />);
    fireEvent.click(await screen.findByRole('button', { name: /backend error/i }));
    fireEvent.click(screen.getByRole('button', { name: 'Clear' }));

    await waitFor(() => expect(clear).toHaveBeenCalled());
    // Re-fetch returned empty -> badge disappears.
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /backend error/i })).not.toBeInTheDocument(),
    );
  });
});
