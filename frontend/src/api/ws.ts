import type { LiveEvent } from './types';
import { tokens } from './client';

/**
 * WebSocket feed for one investigation.
 *
 * Reconnects with exponential backoff so a worker restart or a laptop sleeping does not
 * silently leave the graph stale. The token travels as a query parameter because the
 * browser WebSocket API cannot set an Authorization header.
 */
export class InvestigationSocket {
  private socket: WebSocket | null = null;
  private attempt = 0;
  private closedByUs = false;
  private keepalive: ReturnType<typeof setInterval> | null = null;

  constructor(
    private readonly investigationId: string,
    private readonly onEvent: (event: LiveEvent) => void,
    private readonly onStatus?: (status: 'open' | 'closed' | 'error') => void,
  ) {}

  connect(): void {
    const token = tokens.access();
    if (!token) return;
    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const url = `${protocol}://${window.location.host}/api/v1/ws/investigations/${this.investigationId}?token=${encodeURIComponent(token)}`;

    this.socket = new WebSocket(url);
    this.socket.onopen = () => {
      this.attempt = 0;
      this.onStatus?.('open');
      this.keepalive = setInterval(() => this.socket?.send('ping'), 25_000);
    };
    this.socket.onmessage = (message) => {
      try {
        this.onEvent(JSON.parse(message.data as string) as LiveEvent);
      } catch {
        // A malformed frame is dropped rather than allowed to break the stream.
      }
    };
    this.socket.onerror = () => this.onStatus?.('error');
    this.socket.onclose = () => {
      this.clearKeepalive();
      this.onStatus?.('closed');
      if (this.closedByUs) return;
      const delay = Math.min(30_000, 500 * 2 ** this.attempt++);
      setTimeout(() => this.connect(), delay);
    };
  }

  close(): void {
    this.closedByUs = true;
    this.clearKeepalive();
    this.socket?.close();
    this.socket = null;
  }

  private clearKeepalive(): void {
    if (this.keepalive) clearInterval(this.keepalive);
    this.keepalive = null;
  }
}
