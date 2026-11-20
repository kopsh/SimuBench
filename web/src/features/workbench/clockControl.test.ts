import { describe, expect, it } from "vitest";
import {
  LIVE_RATES,
  isLive,
  liveRate,
  liveRequest,
  pauseMode,
} from "./clockControl";

describe("Live / Pause (#1660)", () => {
  it("calls the wall and the scaled clock live, the others not", () => {
    expect(["wall", "scaled", "command", "stepped"].map(isLive)).toEqual([
      true,
      true,
      false,
      false,
    ]);
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
});
