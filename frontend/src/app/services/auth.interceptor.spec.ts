import { describe, expect, it, beforeEach, vi } from 'vitest';

import { authInterceptor } from './auth.interceptor';

type ReqLike = {
  clone: (opts: { setHeaders: Record<string, string> }) => ReqLike;
  headers: Record<string, string>;
};

function makeReq(): ReqLike {
  const req: ReqLike = {
    headers: {},
    clone(opts) {
      return { ...this, headers: { ...this.headers, ...opts.setHeaders } };
    },
  };
  return req;
}

function runInterceptor(): ReqLike {
  let captured: ReqLike | null = null;
  const next = vi.fn((req: ReqLike) => {
    captured = req;
    return req;
  });
  // The HttpInterceptorFn signature is loose enough at runtime that our
  // structural ReqLike is acceptable.
  authInterceptor(makeReq() as never, next as never);
  if (!captured) {
    throw new Error('next was not called');
  }
  return captured;
}

describe('authInterceptor', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('uses defaults when localStorage is empty', () => {
    const req = runInterceptor();
    expect(req.headers['X-Dev-User']).toBe('dev-user');
    expect(req.headers['X-Dev-Groups']).toBe('admins');
  });

  it('reads X-Dev-User and X-Dev-Groups from localStorage', () => {
    localStorage.setItem('wp.user', 'alice');
    localStorage.setItem('wp.groups', 'auditors,viewers');
    const req = runInterceptor();
    expect(req.headers['X-Dev-User']).toBe('alice');
    expect(req.headers['X-Dev-Groups']).toBe('auditors,viewers');
  });

  it('falls back to default for missing X-Dev-User but keeps custom groups', () => {
    localStorage.setItem('wp.groups', 'operators');
    const req = runInterceptor();
    expect(req.headers['X-Dev-User']).toBe('dev-user');
    expect(req.headers['X-Dev-Groups']).toBe('operators');
  });
});
