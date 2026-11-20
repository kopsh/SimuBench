import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useEventStream, useEvents } from "./events";

// The session stream over a socket the test drives: the hook connects, the test plays the
// server's side (open, messages, close) and reads the URLs the hook asked for.
class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  url: string;
  onopen: (() => void) | null = null;
  onmessage: ((msg: { data: string }) => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }
  close(): void {}
  send(): void {}
}

const reset = () =>
  useEvents.setState({ sid: null, status: "connecting", events: [], version: 0, nowNs: 0, activity: {}, busy: false });

afterEach(() => {
  vi.unstubAllGlobals();
  reset();
});

describe("the session event stream's reconnect cursor (#1632)", () => {
  it("a reconnect asks for the events after the last one held, and the log does not duplicate", async () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const { unmount } = renderHook(() => useEventStream("s1"));

    const first = FakeWebSocket.instances[0];
    expect(first.url).not.toContain("since"); // the first connect takes the whole history
    first.onopen?.();
    first.onmessage?.({ data: JSON.stringify({ seq: 1, t_ns: 1, kind: "scpi", dir: "write", instrument: "awg", text: ":FREQ 2000" }) });
    first.onmessage?.({ data: JSON.stringify({ seq: 2, t_ns: 2, kind: "state", instrument: "awg", path: "ch1.frequency" }) });
    await waitFor(() => expect(useEvents.getState().events).toHaveLength(2));
    expect(useEvents.getState().version).toBe(1); // the state event moved a version-keyed query

    first.onclose?.({ code: 1006 }); // a drop: the hook backs off and reconnects
    await waitFor(() => expect(useEvents.getState().status).toBe("reconnecting"));
    await new Promise((r) => setTimeout(r, 1200)); // the first back-off (1 s) elapses
    const second = FakeWebSocket.instances[1];
    expect(second.url).toContain("since=2"); // the server is asked for the tail only
    second.onopen?.();
    expect(useEvents.getState().version).toBe(2); // the reconnect bump (D64) survives the cursor

    // the server replays nothing already held: what arrives appends exactly once
    second.onmessage?.({ data: JSON.stringify({ seq: 3, t_ns: 3, kind: "scpi", dir: "read", instrument: "awg", text: "OK" }) });
    await waitFor(() => expect(useEvents.getState().events).toHaveLength(3));
    expect(useEvents.getState().events.map((e) => e.seq)).toEqual([1, 2, 3]);

    second.onclose?.({ code: 1006 });
    await new Promise((r) => setTimeout(r, 1200));
    expect(FakeWebSocket.instances[2].url).toContain("since=3"); // the cursor advanced with the log
    unmount();
  });
});

describe("the cursor across a session reset (D91, #1642)", () => {
  it("a reconnect after a reset keeps its cursor: the new session counts on, so nothing is missed", async () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    FakeWebSocket.instances = [];
    const { unmount } = renderHook(() => useEventStream("s1"));

    const first = FakeWebSocket.instances[0];
    first.onopen?.();
    first.onmessage?.({ data: JSON.stringify({ seq: 9, t_ns: 1, kind: "scpi", dir: "write", instrument: "awg", text: ":FREQ 2000" }) });
    first.onmessage?.({ data: JSON.stringify({ seq: 10, t_ns: 2, kind: "scpi", dir: "read", instrument: "awg", text: "OK" }) });
    await waitFor(() => expect(useEvents.getState().events).toHaveLength(2));

    first.onclose?.({ code: 1006 }); // the reset closed the socket; the session id is the same
    await waitFor(() => expect(useEvents.getState().status).toBe("reconnecting"));
    await new Promise((r) => setTimeout(r, 1200));
    const second = FakeWebSocket.instances[1];
    expect(second.url).toContain("since=10"); // the cursor the log holds
    second.onopen?.();

    // the reset session's first events carry seq 11, 12 (the server counts on from the old last):
    // they are after the cursor, so the server sends them and the log keeps them
    second.onmessage?.({ data: JSON.stringify({ seq: 11, t_ns: 1, kind: "scpi", dir: "write", instrument: "awg", text: ":FREQ 3000" }) });
    second.onmessage?.({ data: JSON.stringify({ seq: 12, t_ns: 2, kind: "scpi", dir: "read", instrument: "awg", text: "OK" }) });
    await waitFor(() => expect(useEvents.getState().events).toHaveLength(4));
    expect(useEvents.getState().events.map((e) => e.seq)).toEqual([9, 10, 11, 12]);

    second.onclose?.({ code: 1006 });
    await new Promise((r) => setTimeout(r, 1200));
    expect(FakeWebSocket.instances[2].url).toContain("since=12");
    unmount();
  });
});
