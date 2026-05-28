import { useEffect, useRef } from 'react';

import { currentGroups, currentUser } from '../lib/auth';
import type { AuditEntry } from '../types';

/**
 * Subscribe to the backend's `/ws/events` channel; invoke `onEntry` for each
 * audit event as it arrives. Dev mode passes identity as `?user=&groups=`.
 *
 * Reconnects every 2s if the socket drops. The socket is torn down on unmount.
 * `onEntry` is held in a ref so a changing callback doesn't reopen the socket.
 *
 * Pass `{ enabled: false }` to skip connecting entirely (e.g. a canvas that
 * isn't following an instance) — no socket is opened until enabled flips true.
 */
export function useEvents(
  onEntry: (entry: AuditEntry) => void,
  options: { enabled?: boolean } = {},
): void {
  const { enabled = true } = options;
  const cb = useRef(onEntry);
  cb.current = onEntry;

  useEffect(() => {
    if (!enabled) return;
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let closed = false;

    const connect = (): void => {
      if (closed) return;
      const user = currentUser();
      const groups = currentGroups();
      const proto = location.protocol === 'https:' ? 'wss' : 'ws';
      const url = `${proto}://${location.host}/ws/events?user=${encodeURIComponent(
        user,
      )}&groups=${encodeURIComponent(groups)}`;
      socket = new WebSocket(url);
      socket.onmessage = (event) => {
        try {
          cb.current(JSON.parse(event.data) as AuditEntry);
        } catch {
          // Skip malformed frames; logging from here is intentionally quiet.
        }
      };
      socket.onclose = () => {
        if (closed) return;
        reconnectTimer = setTimeout(connect, 2000);
      };
      socket.onerror = () => {
        // Let onclose handle reconnect; nothing to do here.
      };
    };

    connect();

    return () => {
      closed = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (socket && socket.readyState !== WebSocket.CLOSED) socket.close();
    };
  }, [enabled]);
}
