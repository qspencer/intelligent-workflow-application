import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AuditEntry } from '../types';
import { EventsService } from './events.service';

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
    readyState: 1, // OPEN
    close: vi.fn(),
  };
  createdSockets.push(ws);
  return ws;
}

describe('EventsService', () => {
  beforeEach(() => {
    createdSockets.length = 0;
    localStorage.clear();
    // jsdom doesn't ship a WebSocket; install a fake.
    (globalThis as unknown as { WebSocket: unknown }).WebSocket = vi
      .fn()
      .mockImplementation((url: string) => makeMockSocket(url));
    // Constants on the WebSocket "class"
    Object.assign((globalThis as { WebSocket: { CLOSED: number } }).WebSocket, {
      CLOSED: 3,
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('opens a socket with user + groups from localStorage', () => {
    localStorage.setItem('wp.user', 'alice');
    localStorage.setItem('wp.groups', 'auditors');

    const service = new EventsService();
    const sub = service.stream().subscribe();

    expect(createdSockets).toHaveLength(1);
    expect(createdSockets[0].url).toContain('user=alice');
    expect(createdSockets[0].url).toContain('groups=auditors');
    expect(createdSockets[0].url).toContain('/ws/events');

    sub.unsubscribe();
  });

  it('falls back to defaults when localStorage is empty', () => {
    const service = new EventsService();
    const sub = service.stream().subscribe();
    expect(createdSockets[0].url).toContain('user=dev-user');
    expect(createdSockets[0].url).toContain('groups=admins');
    sub.unsubscribe();
  });

  it('emits parsed AuditEntry objects on incoming messages', () => {
    const received: AuditEntry[] = [];
    const service = new EventsService();
    const sub = service.stream().subscribe((e) => received.push(e));

    const entry: AuditEntry = {
      id: 'a-1',
      timestamp: '2026-05-18T10:00:00Z',
      actor_type: 'engine',
      actor_id: 'workflow_engine',
      action: 'workflow_started',
      workflow_instance_id: 'i-1',
      step_id: null,
      detail: {},
    };
    createdSockets[0].onmessage!({ data: JSON.stringify(entry) } as MessageEvent);

    expect(received).toEqual([entry]);
    sub.unsubscribe();
  });

  it('ignores malformed messages instead of erroring', () => {
    const received: AuditEntry[] = [];
    const service = new EventsService();
    const sub = service.stream().subscribe({
      next: (e) => received.push(e),
      error: () => {
        throw new Error('should not emit error');
      },
    });
    createdSockets[0].onmessage!({ data: 'not json' } as MessageEvent);
    expect(received).toEqual([]);
    sub.unsubscribe();
  });

  it('reconnects after the socket closes', () => {
    vi.useFakeTimers();
    const service = new EventsService();
    const sub = service.stream().subscribe();
    expect(createdSockets).toHaveLength(1);

    createdSockets[0].onclose!();
    vi.advanceTimersByTime(2000);
    expect(createdSockets).toHaveLength(2);

    sub.unsubscribe();
  });

  it('does not reconnect once unsubscribed', () => {
    vi.useFakeTimers();
    const service = new EventsService();
    const sub = service.stream().subscribe();
    sub.unsubscribe();

    createdSockets[0].onclose!();
    vi.advanceTimersByTime(5000);
    expect(createdSockets).toHaveLength(1);
  });

  it('closes the socket on unsubscribe', () => {
    const service = new EventsService();
    const sub = service.stream().subscribe();
    // Pretend the socket is still open (readyState != CLOSED).
    sub.unsubscribe();
    expect(createdSockets[0].close).toHaveBeenCalledTimes(1);
  });
});
