import { describe, expect, it } from "vitest";
import type { InstrumentInfo, SessionInfo, StateVarInfo } from "./client";
import { withValues } from "./sessionValues";

const v = (path: string, value: unknown) => ({ path, value, type: "float", options: null }) as unknown as StateVarInfo;
const inst = (name: string, state: StateVarInfo[], values: Record<string, unknown> | null = null) =>
  ({ name, definition_id: `def-${name}`, state, values, remote: [] }) as unknown as InstrumentInfo;
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
    expect(withValues({ ...held, build: "" }, session("", [inst("scope", [], { "timebase.scale": 1, "ch1.scale": 1 })]))).toBeNull();
  });
});
