import { useEffect } from "react";
import { create } from "zustand";
import type { Event } from "./generated/events";
import { openWsStream, wsUrl, type ConnectionState } from "./wsTransport";

export type { ConnectionState };

const MAX_EVENTS = 100_000;
/** How long after a command the session still counts as running commands. */
const BUSY_MS = 800;
let busyTimer: number | undefined;

interface EventStore {
  sid: string | null;
  status: ConnectionState;
  events: Event[];
  /** Bumped on every state-changing event so screens can refresh. */
  version: number;
  nowNs: number;
  /** Commands written to each instrument so far: a change makes its canvas node flash (D44). */
  activity: Record<string, number>;
  /** A command arrived in the last moment: "running commands" rather than "waiting" (D44). */
  busy: boolean;
  clear: () => void;
}

export const useEvents = create<EventStore>((set) => ({
  sid: null,
  status: "connecting",
  events: [],
  version: 0,
  nowNs: 0,
  activity: {},
  busy: false,
  clear: () => set({ events: [] }),
}));

/** Keep a WebSocket to the session's event stream, reconnecting with back-off.
 *
 * A reconnect carries the ``since`` cursor of the last event delivered (#1632): the server
 * replays only the events after it, so nothing already held arrives twice and the log keeps
 * its size across the drop. The first connect omits the cursor and gets the whole history.
 */
export function useEventStream(sid: string, onState?: (ev: Event) => void) {
  useEffect(() => {
    let lastSeq = 0; // the newest event delivered: the reconnect's since (#1632)
    useEvents.setState({ sid, status: "connecting", events: [], version: 0, activity: {}, busy: false });

    const pending: Event[] = [];
    let flushScheduled = false;
    const flush = () => {
      flushScheduled = false;
      if (!pending.length) return;
      const batch = pending.splice(0);
      if (batch.some((e) => e.kind === "scpi" && e.dir === "write")) {
        window.clearTimeout(busyTimer);
        busyTimer = window.setTimeout(() => useEvents.setState({ busy: false }), BUSY_MS);
      }
      useEvents.setState((s) => {
        const events = s.events.length + batch.length > MAX_EVENTS ? [...s.events, ...batch].slice(-MAX_EVENTS) : [...s.events, ...batch];
        const changed = batch.some((e) => e.kind !== "scpi" || e.dir === "write");
        const writes = batch.filter((e) => e.kind === "scpi" && e.dir === "write" && e.instrument);
        let activity = s.activity;
        if (writes.length) {
          activity = { ...activity };
          for (const e of writes) activity[e.instrument!] = (activity[e.instrument!] ?? 0) + 1;
        }
        return {
          events,
          activity,
          busy: writes.length ? true : s.busy,
          version: changed ? s.version + 1 : s.version,
          nowNs: Math.max(s.nowNs, ...batch.map((e) => e.t_ns + (e.duration_ns ?? 0))),
        };
      });
    };

    return openWsStream(
      () => wsUrl(`/api/sessions/${sid}/events`, lastSeq > 0 ? `&since=${lastSeq}` : ""),
      {
        onOpen: (reconnect) => {
          // A reconnect still re-reads what the version-keyed queries hold (D64): the stream
          // replays the events after the cursor, but an answer a query already fetched may
          // predate them. The first open must not bump — every query just mounted on version 0.
          useEvents.setState((s) => ({ status: "connected", version: reconnect ? s.version + 1 : s.version }));
        },
        onMessage: (data) => {
          const ev = JSON.parse(data) as Event;
          lastSeq = Math.max(lastSeq, ev.seq);
          pending.push(ev);
          if (ev.kind === "state" && onState) onState(ev);
          if (!flushScheduled) {
            flushScheduled = true;
            requestAnimationFrame(flush);
          }
        },
        onClose: (code) => (code === 4404 ? "gone" : undefined),
        onStatus: (status) => useEvents.setState({ status }),
      },
    );
  }, [sid, onState]);
}

/**
 * Wall-clock and scaled-clock sessions move on their own, without events: count the clock forward in real time from
 * the session's last reported instant, and nudge screens to re-read twice a second.
 */
export function useWallClock(clockMode: string, serverNowNs: number, scale = 1) {
  useEffect(() => {
    if (clockMode !== "wall" && clockMode !== "scaled") return;
    const rate = clockMode === "scaled" ? scale : 1;
    const anchorNs = serverNowNs;
    const anchorMs = performance.now();
    let ticks = 0;
    const timer = window.setInterval(() => {
      ticks += 1;
      const now = anchorNs + Math.round((performance.now() - anchorMs) * 1e6 * rate);
      useEvents.setState((s) => ({ nowNs: Math.max(s.nowNs, now), version: ticks % 5 === 0 ? s.version + 1 : s.version }));
    }, 100);
    return () => window.clearInterval(timer);
  }, [clockMode, serverNowNs, scale]);
}

