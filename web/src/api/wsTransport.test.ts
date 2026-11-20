import { afterEach, describe, expect, it, vi } from "vitest";
import { backoffMs, openWsStream, wsUrl, type ConnectionState } from "./wsTransport";

// The transport over a socket and a clock the test drives: open, drop, advance — the streams'
// own message semantics live in their files, here only the lifecycle they both ride (#1733).
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

const harness = () => {
  const states: ConnectionState[] = [];
  const stop = openWsStream(() => wsUrl("/api/x"), {
    onOpen: () => {},
    onMessage: () => {},
    onStatus: (s) => states.push(s),
  });
  return { states, stop };
};

/** The reconnect the current timer has just fired (or nothing when it has not). */
const sockets = () => FakeWebSocket.instances.length;

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  FakeWebSocket.instances = [];
});

describe("the reconnect back-off (#1733)", () => {
  it("doubles from 1 s and caps at 10 s", () => {
    expect([1, 2, 3, 4, 5, 6, 9].map(backoffMs)).toEqual([1_000, 2_000, 4_000, 8_000, 10_000, 10_000, 10_000]);
  });

  it("waits the back-off between drops, reconnecting exactly on schedule", () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    vi.useFakeTimers();
    const { states } = harness();

    for (const ms of [1_000, 2_000, 4_000, 8_000, 10_000, 10_000]) {
      const n = sockets();
      FakeWebSocket.instances.at(-1)!.onclose?.({ code: 1006 });
      expect(states.at(-1)).toBe("reconnecting");
      vi.advanceTimersByTime(ms - 1);
      expect(sockets()).toBe(n); // still waiting
      vi.advanceTimersByTime(1);
      expect(sockets()).toBe(n + 1); // the retry fired exactly then
    }
  });

  it("a held connection resets the doubling: the next drop waits 1 s again", () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    vi.useFakeTimers();
    harness();

    FakeWebSocket.instances[0].onclose?.({ code: 1006 }); // two drops: the timer sits at 2 s
    vi.advanceTimersByTime(2_000);
    FakeWebSocket.instances[1].onopen?.(); // held: the doubling restarts from here
    FakeWebSocket.instances[1].onclose?.({ code: 1006 });
    vi.advanceTimersByTime(999);
    expect(sockets()).toBe(2);
    vi.advanceTimersByTime(1);
    expect(sockets()).toBe(3); // 1 s, not 4 s
  });
});

describe("the close codes the transport owns", () => {
  it("4401 reports unauthorized and stops retrying", () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    vi.useFakeTimers();
    const { states } = harness();

    FakeWebSocket.instances[0].onclose?.({ code: 4401 });
    expect(states).toEqual(["unauthorized"]);
    vi.advanceTimersByTime(60_000);
    expect(sockets()).toBe(1); // no reconnect follows
  });

  it("a close code the stream itself owns is reported instead", () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    vi.useFakeTimers();
    const states: ConnectionState[] = [];
    openWsStream(() => wsUrl("/api/x"), {
      onOpen: () => {},
      onMessage: () => {},
      onClose: (code) => (code === 4404 ? "gone" : undefined), // the session stream's 4404
      onStatus: (s) => states.push(s),
    });

    FakeWebSocket.instances[0].onclose?.({ code: 4404 });
    expect(states).toEqual(["gone"]);
    vi.advanceTimersByTime(60_000);
    expect(sockets()).toBe(1); // the transport stands down on the stream's own answer
  });
});

describe("the lifecycle", () => {
  it("the first open is not a reconnect; every later open is; messages pass through as text", () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    vi.useFakeTimers();
    const opens: boolean[] = [];
    const messages: string[] = [];
    openWsStream(() => wsUrl("/api/x"), {
      onOpen: (reconnect) => opens.push(reconnect),
      onMessage: (data) => messages.push(data),
      onStatus: () => {},
    });

    FakeWebSocket.instances[0].onopen?.();
    expect(opens).toEqual([false]);
    FakeWebSocket.instances[0].onmessage?.({ data: "hi" });
    expect(messages).toEqual(["hi"]);
    FakeWebSocket.instances[0].onclose?.({ code: 1006 });
    vi.advanceTimersByTime(1_000);
    FakeWebSocket.instances[1].onopen?.();
    expect(opens).toEqual([false, true]);
  });

  it("the cleanup cancels a scheduled retry, and a socket dying after it reconnects nothing", () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    vi.useFakeTimers();
    const { stop } = harness();

    FakeWebSocket.instances[0].onclose?.({ code: 1006 }); // a drop schedules the retry
    stop(); // the stream effect's cleanup
    vi.advanceTimersByTime(60_000);
    expect(sockets()).toBe(1); // the timer was cleared
  });

  it("a socket closing after the cleanup does not reconnect either", () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    vi.useFakeTimers();
    const { stop } = harness();

    FakeWebSocket.instances[0].onopen?.();
    stop();
    FakeWebSocket.instances[0].onclose?.({ code: 1006 }); // the unmount's own close lands later
    vi.advanceTimersByTime(60_000);
    expect(sockets()).toBe(1);
  });
});

describe("the stream URL", () => {
  it("is ws on this origin with the token slot and the stream's extra query", () => {
    expect(wsUrl("/api/events", "&since=7")).toBe(`ws://${window.location.host}/api/events?token=&since=7`);
  });
});
