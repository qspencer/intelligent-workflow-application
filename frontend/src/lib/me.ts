/** Memoized `/api/me` — one fetch per page load, shared by the header chip,
 * the RoleSwitcher gate, and the Users nav link. Resolves null on any error
 * (unauthenticated, backend down): callers fall back to their default. */

import { api } from '../api/client';
import type { Me } from '../types';

let cached: Promise<Me | null> | null = null;

export function getMe(): Promise<Me | null> {
  cached ??= api.me().catch(() => null);
  return cached;
}

export function resetMe(): void {
  cached = null;
}
