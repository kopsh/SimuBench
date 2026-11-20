import { describe, expect, it } from "vitest";
import type { InstrumentInfo, SessionInfo, StateVarInfo } from "./client";
import type { Event } from "./generated/events";
import { withStateEvent, withValues } from "./sessionValues";

const v = (path: string, value: unknown) => ({ path, value, type: "float", options: null }) as unknown as StateVarInfo;
const inst = (name: string, state: StateVarInfo[], values: Record<string, unknown> | null = null, spec_rev = 0) =>
  ({ name, definition_id: `def-${name}`, state, values, spec_rev, remote: [] }) as unknown as InstrumentInfo;
const session = (build: string, instruments: InstrumentInfo[], now_ns = 0) => ({ id: "s", build, now_ns, instruments }) as unknown as SessionInfo;

describe("polling a session's values (#1299)", () => {
  const held = session("b1", [inst("scope", [v("timebase.scale", 1e-6), v("ch1.scale", 0.5)])]);

  it("lays the values onto the held settings, keeping the unchanged ones as they were", () => {
    const light = session("b1", [inst("scope", [], { "timebase.scale": 2e-6, "ch1.scale": 0.5 })], 42);
    const merged = withValues(held, light)!;
    expect(merged.now_ns).toBe(42);
    const [scale, ch1] = merged.instruments[0].state;
    expect(scale.value).toBe(2e-6);
    expect(ch1).toBe(held.instruments[0].state[1]);
    expect(merged.instruments[0].values).toBeNull();
  });

  it("asks for the full session after another build, or when the settings do not match", () => {
    expect(withValues(held, session("b2", [inst("scope", [], { "timebase.scale": 1, "ch1.scale": 1 })]))).toBeNull();
    expect(withValues(held, session("b1", [inst("scope", [], { "timebase.scale": 1 })]))).toBeNull();
    expect(withValues(held, session("b1", [inst("scope", [], { "timebase.scale": 1, "ch2.scale": 1 })]))).toBeNull();
    expect(withValues(held, session("b1", [inst("awg", [], { "timebase.scale": 1, "ch1.scale": 1 })]))).toBeNull();
    // A limit moved at run time in the same build (a power supply's series pair, #1302).
    expect(withValues(held, session("b1", [inst("scope", [], { "timebase.scale": 1e-6, "ch1.scale": 0.5 }, 1)]))).toBeNull();
    expect(withValues({ ...held, build: "" }, session("", [inst("scope", [], { "timebase.scale": 1, "ch1.scale": 1 })]))).toBeNull();
  });
});

describe("a state event laid onto the held session (#1730)", () => {
  const held = session("b1", [
    inst("scope", [v("timebase.scale", 1e-6), v("ch1.scale", 0.5)]),
    inst("awg", [v("output.load", 50)]),
  ]);
  const ev = (instrument: string | null, path: string | null, value: unknown) =>
    ({ kind: "state", instrument, path, new: value, seq: 1, t_ns: 0 }) as unknown as Event;

  it("a hit changes only the named variable's value; the rest keep their objects", () => {
    const merged = withStateEvent(held, ev("scope", "timebase.scale", 2e-6));
    const [scale, ch1] = merged.instruments[0].state;
    expect(scale.value).toBe(2e-6);
    // Only the value moved; every other field of the variable, its siblings, the other
    // instruments and the session itself stay as they were.
    expect(scale.path).toBe(held.instruments[0].state[0].path);
    expect(scale.type).toBe(held.instruments[0].state[0].type);
    expect(ch1).toBe(held.instruments[0].state[1]);
    expect(merged.instruments[1]).toBe(held.instruments[1]);
    expect(merged.build).toBe(held.build);
    expect(merged.instruments.length).toBe(held.instruments.length);
  });

  it("an instrument or path the cache does not hold returns the held session as it was", () => {
    expect(withStateEvent(held, ev("awg", "timebase.scale", 1))).toBe(held);
    expect(withStateEvent(held, ev("scope", "no.such.path", 1))).toBe(held);
    // An event without its instrument or path cannot name a variable at all.
    expect(withStateEvent(held, ev(null, "timebase.scale", 1))).toBe(held);
    expect(withStateEvent(held, ev("scope", null, 1))).toBe(held);
  });

  it("a value that did not change keeps the held session (the same() rule of withValues)", () => {
    expect(withStateEvent(held, ev("scope", "timebase.scale", 1e-6))).toBe(held);
    // Lists and objects of the JSON kinds count as values too.
    expect(withStateEvent(held, ev("scope", "ch1.scale", 0.5))).toBe(held);
    expect(withStateEvent(held, ev("scope", "timebase.scale", { a: [1, 2] }))).not.toBe(held);
  });
});
