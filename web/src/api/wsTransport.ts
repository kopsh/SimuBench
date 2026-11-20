// The one WebSocket transport both event streams ride (#1733): connect, back off on a drop,
// report the state. The streams keep only their own onmessage semantics (the session log's
// batched flush, the workspace's reducer); the lifecycle and its knobs — the back-off formula,
// the authed URL, the 4401 branch — live here once.
import { getToken } from "./token";

/** Where a stream's connection stands, as the UI reports it. */
export type ConnectionState = "connecting" | "connected" | "reconnecting" | "unauthorized" | "gone";

/** The reconnect delay after the nth failed attempt: 1 s, doubling, capped at 10 s. */
export const backoffMs = (attempt: number) => Math.min(10_000, 500 * 2 ** Math.min(attempt, 5));

/** A stream's URL on this origin, carrying the access token in the query parameter — a browser
 * WebSocket cannot set a header (the rule lives in token.ts, #1735). */
export const wsUrl = (path: string, query = "") => {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}${path}?token=${encodeURIComponent(getToken() ?? "")}${query}`;
};

export interface WsStreamHandlers {
  /** The socket opened: set "connected" (atomically with whatever the stream also holds) and
   * do the stream's own open work; `reconnect` says an earlier attempt had dropped. */
  onOpen: (reconnect: boolean) => void;
  /** One message's payload as text. */
  onMessage: (data: string) => void;
  /** A close code only this stream interprets (the session's 4404 → "gone"): return the state
   * to report and the transport stops there. Unhandled codes fall through to the defaults. */
  onClose?: (code: number) => ConnectionState | undefined;
  /** The socket dropped without the stream's own meaning: report it (then the transport backs off). */
  onStatus: (status: ConnectionState) => void;
}

/** Keep one WebSocket to a stream: connect now, and on every drop report the state and
 * reconnect after the back-off. Returns the cleanup the stream's effect returns. */
export function openWsStream(url: () => string, handlers: WsStreamHandlers): () => void {
  let ws: WebSocket | null = null;
  let closed = false;
  let attempt = 0;
  let timer: number | undefined;
  const connect = () => {
    ws = new WebSocket(url());
    ws.onopen = () => {
      handlers.onOpen(attempt > 0);
      attempt = 0;
    };
    ws.onmessage = (msg) => handlers.onMessage(msg.data as string);
    ws.onclose = (e) => {
      if (closed) return;
      const own = handlers.onClose?.(e.code);
      if (own) return handlers.onStatus(own);
      if (e.code === 4401) return handlers.onStatus("unauthorized");
      handlers.onStatus("reconnecting");
      attempt += 1;
      timer = window.setTimeout(connect, backoffMs(attempt));
    };
  };
  connect();
  return () => {
    closed = true;
    if (timer) window.clearTimeout(timer);
    ws?.close();
  };
}
