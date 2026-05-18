import { Injectable } from '@angular/core';
import { Observable, Subject } from 'rxjs';

import { AuditEntry } from '../types';

/**
 * Subscribes to the backend's `/ws/events` channel and emits each audit
 * event as it arrives. Dev mode reads identity from localStorage and
 * passes it as `?user=&groups=`.
 *
 * Reconnects every 2s if the socket drops. Caller can also `complete()` to
 * tear everything down.
 */
@Injectable({ providedIn: 'root' })
export class EventsService {
  /** Open a stream of audit events. Caller unsubscribes to close. */
  stream(): Observable<AuditEntry> {
    return new Observable<AuditEntry>((subscriber) => {
      let socket: WebSocket | null = null;
      let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
      let closed = false;

      const connect = (): void => {
        if (closed) return;
        const user = localStorage.getItem('wp.user') ?? 'dev-user';
        const groups = localStorage.getItem('wp.groups') ?? 'admins';
        const proto = location.protocol === 'https:' ? 'wss' : 'ws';
        const url = `${proto}://${location.host}/ws/events?user=${encodeURIComponent(
          user,
        )}&groups=${encodeURIComponent(groups)}`;
        socket = new WebSocket(url);
        socket.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data) as AuditEntry;
            subscriber.next(data);
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
        if (socket && socket.readyState !== WebSocket.CLOSED) {
          socket.close();
        }
      };
    });
  }

  /**
   * Test seam: lets tests inject a fake event source. Default real impl just
   * delegates to `stream()`.
   */
  protected create(): Subject<AuditEntry> {
    return new Subject<AuditEntry>();
  }
}
