import { useEffect } from "react";
import { create } from "zustand";
import type { Event } from "./generated/events";
import { getToken } from "./token";

export type ConnectionState = "connecting" | "connected" | "reconnecting" | "unauthorized" | "gone";

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

/** Keep a WebSocket to the session's event stream, reconnecting with back-off. */
export function useEventStream(sid: string, onState?: (ev: Event) => void) {
  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;
    let attempt = 0;
    let timer: number | undefined;
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

    const connect = () => {
      const proto = window.location.protocol === "https:" ? "wss" : "ws";
      const url = `${proto}://${window.location.host}/api/sessions/${sid}/events?token=${encodeURIComponent(getToken() ?? "")}`;
      ws = new WebSocket(url);
      ws.onopen = () => {
        attempt = 0;
        useEvents.setState({ status: "connected" });
      };
      ws.onmessage = (msg) => {
        const ev = JSON.parse(msg.data as string) as Event;
        pending.push(ev);
        if (ev.kind === "state" && onState) onState(ev);
        if (!flushScheduled) {
          flushScheduled = true;
          requestAnimationFrame(flush);
        }
      };
      ws.onclose = (e) => {
        if (closed) return;
        if (e.code === 4401) return useEvents.setState({ status: "unauthorized" });
        if (e.code === 4404) return useEvents.setState({ status: "gone" });
        useEvents.setState({ status: "reconnecting" });
        attempt += 1;
        timer = window.setTimeout(connect, Math.min(10_000, 500 * 2 ** Math.min(attempt, 5)));
      };
    };
    connect();
    return () => {
      closed = true;
      if (timer) window.clearTimeout(timer);
      ws?.close();
    };
  }, [sid, onState]);
}

/**
 * Wall-clock sessions move on their own, without events: count the clock forward in real time from
 * the session's last reported instant, and nudge screens to re-read twice a second.
 */
export function useWallClock(clockMode: string, serverNowNs: number) {
  useEffect(() => {
    if (clockMode !== "wall") return;
    const anchorNs = serverNowNs;
    const anchorMs = performance.now();
    let ticks = 0;
    const timer = window.setInterval(() => {
      ticks += 1;
      const now = anchorNs + Math.round((performance.now() - anchorMs) * 1e6);
      useEvents.setState((s) => ({ nowNs: Math.max(s.nowNs, now), version: ticks % 5 === 0 ? s.version + 1 : s.version }));
    }, 100);
    return () => window.clearInterval(timer);
  }, [clockMode, serverNowNs]);
}

