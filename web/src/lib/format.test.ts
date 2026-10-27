import { describe, expect, it } from "vitest";
import { formatClock, formatEng, parseEngineering } from "./format";

describe("format", () => {
  it("engineering notation", () => {
    expect(formatEng(1591.55, "Hz")).toBe("1.592 kHz");
    expect(formatEng(0.05, "V")).toBe("50 mV");
    expect(formatEng(-6.1e-6, "V")).toBe("−6.1 µV");
    expect(formatEng(0, "V")).toBe("0 V");
    expect(formatEng(Infinity, "Ω")).toBe("∞ Ω");
  });
  it("clock", () => {
    expect(formatClock(4_200_000_000)).toBe("00:00:04.200");
    expect(formatClock(3_723_004_000_000)).toBe("01:02:03.004");
  });
  it("parses user input", () => {
    expect(parseEngineering("1.5k")).toBe(1500);
    expect(parseEngineering("20 mV")).toBeCloseTo(0.02);
    expect(parseEngineering("1e-3")).toBe(0.001);
    expect(parseEngineering("INF")).toBe(Infinity);
    expect(parseEngineering("abc")).toBeNull();
  });
});
