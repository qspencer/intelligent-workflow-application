import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

import { api, ApiError } from '../api/client';
import { LoginPage } from './LoginPage';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function fill(email: string, password: string): void {
  fireEvent.change(screen.getByLabelText('Email'), { target: { value: email } });
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: password } });
}

describe('LoginPage', () => {
  it('submits credentials and reloads on success', async () => {
    const login = vi.spyOn(api, 'login').mockResolvedValue({ ok: true });
    const reload = vi.fn();
    const original = window.location;
    Object.defineProperty(window, 'location', {
      value: { ...original, reload },
      writable: true,
    });
    render(<LoginPage />);
    fill('alice@example.com', 'correct horse');
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    await waitFor(() => expect(reload).toHaveBeenCalled());
    expect(login).toHaveBeenCalledWith('alice@example.com', 'correct horse');
    Object.defineProperty(window, 'location', { value: original, writable: true });
  });

  it('shows the backend error as an alert and re-enables the form', async () => {
    vi.spyOn(api, 'login').mockRejectedValue(
      new ApiError(401, 'Invalid email or password', 'Invalid email or password'),
    );
    render(<LoginPage />);
    fill('alice@example.com', 'wrong');
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toBe('Invalid email or password');
    expect(screen.getByRole('button', { name: 'Sign in' })).not.toBeDisabled();
  });
});
