import { describe, expect, it } from "vitest";
import { formatClock, formatEng, formatReadout, isOverload, parseEngineering, resistance } from "./format";

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

const text = (r: { mantissa: string; prefix: string; unit: string }) => `${r.mantissa} ${r.prefix}${r.unit}`;

describe("formatReadout (#313)", () => {
  it("source-measure display: fixed digits with the sign", () => {
    expect(text(formatReadout(1, { unit: "V", digits: 6, fixed: true, signed: true }))).toBe("+1.00000 V");
    expect(text(formatReadout(0, { unit: "A", digits: 6, fixed: true, signed: true }))).toBe("+0.00000 A");
    expect(text(formatReadout(-0.0105, { unit: "A", digits: 6, fixed: true, signed: true }))).toBe("-10.5000 mA");
  });
  it("6½-digit multimeter: seven significant digits", () => {
    expect(text(formatReadout(9.9990002, { unit: "V", digits: 7, fixed: true }))).toBe("9.999000 V");
    expect(text(formatReadout(0.1, { unit: "V", digits: 7, fixed: true }))).toBe("100.0000 mV");
  });
  it("counter: counts are whole numbers, statistics carry their unit", () => {
    expect(formatReadout(1000, { count: true }).mantissa).toBe("1000");
    expect(formatReadout(12.0000001, { count: true }).mantissa).toBe("12");
    expect(text(formatReadout(1000.0012, { unit: "Hz", digits: 10, fixed: true }))).toBe("1.000001200 kHz");
  });
  it("overload: 9.9E+37 and 9.91E+37 both, and non-finite values", () => {
    for (const v of [9.9e37, 9.91e37, -9.9e37, Infinity, NaN]) {
      expect(isOverload(v)).toBe(true);
      expect(formatReadout(v, { unit: "V" }).overload).toBe(true);
    }
    expect(isOverload(1e30)).toBe(false);
  });
  it("resistance with no current is overload, not 0 Ω", () => {
    expect(isOverload(resistance(1, 0))).toBe(true);
    expect(text(formatReadout(resistance(5, 0.005), { unit: "Ω", digits: 5, fixed: true, signed: true }))).toBe("+1.0000 kΩ");
  });
});
