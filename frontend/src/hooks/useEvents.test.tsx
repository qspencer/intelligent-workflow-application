import { cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useEvents } from './useEvents';
import type { AuditEntry } from '../types';

interface MockWebSocket {
  onmessage: ((event: MessageEvent) => void) | null;
  onclose: (() => void) | null;
  onerror: (() => void) | null;
  readyState: number;
  close: ReturnType<typeof vi.fn>;
  url: string;
}

const createdSockets: MockWebSocket[] = [];

function makeMockSocket(url: string): MockWebSocket {
  const ws: MockWebSocket = {
    url,
    onmessage: null,
    onclose: null,
    onerror: null,
    readyState: 1,
    close: vi.fn(),
  };
  createdSockets.push(ws);
  return ws;
}

function Harness({ onEntry }: { onEntry: (e: AuditEntry) => void }) {
  useEvents(onEntry);
  return null;
}

const sampleEntry: AuditEntry = {
  id: 'a-1',
  timestamp: '2026-05-18T10:00:00Z',
  actor_type: 'engine',
  actor_id: 'workflow_engine',
  action: 'workflow_started',
  workflow_instance_id: 'i-1',
  step_id: null,
  detail: {},
};

describe('useEvents', () => {
  beforeEach(() => {
    createdSockets.length = 0;
    localStorage.clear();
    // Regular function (not an arrow) so the mock is constructable: the hook
    // does `new WebSocket(url)`, and vitest 4 throws "is not a constructor" on
    // `new` of an arrow-fn mock. A constructor returning an object yields that
    // object as the instance, so `new WebSocket(url)` returns the mock socket.
    (globalThis as unknown as { WebSocket: unknown }).WebSocket = vi
      .fn()
      .mockImplementation(function (url: string) {
        return makeMockSocket(url);
      });
    Object.assign((globalThis as { WebSocket: { CLOSED: number } }).WebSocket, {
      CLOSED: 3,
    });
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it('opens a socket with user + groups from localStorage', () => {
    localStorage.setItem('wp.user', 'alice');
    localStorage.setItem('wp.groups', 'auditors');
    render(<Harness onEntry={() => {}} />);
    expect(createdSockets).toHaveLength(1);
    expect(createdSockets[0].url).toContain('user=alice');
    expect(createdSockets[0].url).toContain('groups=auditors');
    expect(createdSockets[0].url).toContain('/ws/events');
  });

  it('falls back to defaults when localStorage is empty', () => {
    render(<Harness onEntry={() => {}} />);
    expect(createdSockets[0].url).toContain('user=dev-user');
    expect(createdSockets[0].url).toContain('groups=admins');
  });

  it('invokes the callback with parsed entries', () => {
    const received: AuditEntry[] = [];
    render(<Harness onEntry={(e) => received.push(e)} />);
    createdSockets[0].onmessage!({ data: JSON.stringify(sampleEntry) } as MessageEvent);
    expect(received).toEqual([sampleEntry]);
  });

  it('ignores malformed frames', () => {
    const received: AuditEntry[] = [];
    render(<Harness onEntry={(e) => received.push(e)} />);
    createdSockets[0].onmessage!({ data: 'not json' } as MessageEvent);
    expect(received).toEqual([]);
  });

  it('reconnects after the socket closes', () => {
    vi.useFakeTimers();
    render(<Harness onEntry={() => {}} />);
    expect(createdSockets).toHaveLength(1);
    createdSockets[0].onclose!();
    vi.advanceTimersByTime(2000);
    expect(createdSockets).toHaveLength(2);
  });

  it('does not reconnect after unmount', () => {
    vi.useFakeTimers();
    const { unmount } = render(<Harness onEntry={() => {}} />);
    unmount();
    createdSockets[0].onclose?.();
    vi.advanceTimersByTime(5000);
    expect(createdSockets).toHaveLength(1);
  });

  it('closes the socket on unmount', () => {
    const { unmount } = render(<Harness onEntry={() => {}} />);
    unmount();
    expect(createdSockets[0].close).toHaveBeenCalledTimes(1);
  });
});
