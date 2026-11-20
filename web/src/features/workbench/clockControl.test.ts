import { describe, expect, it } from "vitest";
import i18n, { type T } from "@/i18n";
import { LIVE_RATES, clockStateLabel, isLive, liveRate, liveRequest, pauseMode } from "./clockControl";

describe("Live / Pause (#1660)", () => {
  it("calls the wall and the scaled clock live, the others not", () => {
    expect(["wall", "scaled", "command", "stepped"].map(isLive)).toEqual([true, true, false, false]);
  });
  it("asks for the wall clock at 1× and a scaled clock at any other rate", () => {
    expect(LIVE_RATES[0]).toBe(1);
    expect(liveRequest(1)).toEqual({ mode: "wall" });
    expect(liveRequest(100)).toEqual({ mode: "scaled", scale: 100 });
  });
  it("reads the rate of a running clock: 1 on the wall clock", () => {
    expect(liveRate("wall", 1)).toBe(1);
    expect(liveRate("scaled", 10)).toBe(10);
    expect(liveRate("command", 50)).toBe(1);
  });
  it("pauses onto the stepped clock a bench opened on, else the command clock", () => {
    expect(pauseMode("stepped")).toBe("stepped");
    expect(pauseMode("command")).toBe("command");
    expect(pauseMode("wall")).toBe("command"); // a bench that opened live
    expect(pauseMode(undefined)).toBe("command");
  });
  it("names the clock by what it does, not by its mode", () => {
    const t = i18n.getFixedT("zh") as T;
    expect(clockStateLabel("wall", 1, t)).toBe("实时");
    expect(clockStateLabel("scaled", 10, t)).toBe("实时 10×");
    expect(clockStateLabel("command", 1, t)).toBe("已暂停");
    expect(clockStateLabel("stepped", 1, t)).toBe("已暂停");
  });
});
