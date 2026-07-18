import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';

import { Skeleton } from './Skeleton';

afterEach(cleanup);

describe('Skeleton', () => {
  it('is a polite live region with screen-reader text', () => {
    render(<Skeleton />);
    const region = screen.getByRole('status');
    expect(region.getAttribute('aria-live')).toBe('polite');
    expect(screen.getByText('Loading…').className).toBe('visually-hidden');
  });

  it('sketches the requested number of table rows plus a header bar', () => {
    const { container } = render(<Skeleton count={6} />);
    expect(container.querySelectorAll('.skeleton-bar')).toHaveLength(7);
  });

  it('renders cards in the shared card grid so layout matches the loaded page', () => {
    const { container } = render(<Skeleton variant="cards" count={3} />);
    expect(container.querySelector('.card-grid')).not.toBeNull();
    expect(container.querySelectorAll('.skeleton-card')).toHaveLength(3);
  });

  it('hides the decorative bars from assistive tech', () => {
    const { container } = render(<Skeleton variant="detail" />);
    const bars = container.querySelector('[aria-hidden="true"]');
    expect(bars?.querySelector('.skeleton-bar')).not.toBeNull();
  });
});
