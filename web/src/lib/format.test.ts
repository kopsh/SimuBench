import { describe, expect, it } from "vitest";
import { displayUnit, engParts, formatClock, formatEng, formatReadout, isOverload, makerName, parseEngineering, resistance, unitWord } from "./format";

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

// Values at an SI-prefix boundary (#1459); the same table is in tests/test_tui_units.py, so the
// digits-kept readout and the TUI say the same, and the trimmed label never shows an exponent.
const EDGE_VALUES = [0.99994, 0.9996, 999.7, 0.0009996, 0.999996, 999.996, 0.0099996];
const PREFIX_EDGES: Record<number, string[]> = {
  3: ["1.00 V", "1.00 V", "1.00 kV", "1.00 mV", "1.00 V", "1.00 kV", "10.0 mV"],
  4: ["999.9 mV", "999.6 mV", "999.7 V", "999.6 µV", "1.000 V", "1.000 kV", "10.00 mV"],
  5: ["999.94 mV", "999.60 mV", "999.70 V", "999.60 µV", "1.0000 V", "1.0000 kV", "9.9996 mV"],
};

describe("engineering prefix boundaries (#1459)", () => {
  it.each([3, 4, 5])("rounds before it picks the prefix at %i digits", (digits) => {
    expect(EDGE_VALUES.map((v) => text(engParts(v, "V", digits, true)))).toEqual(PREFIX_EDGES[digits]);
    for (const v of EDGE_VALUES) expect(formatEng(v, "V", digits).includes("e")).toBe(false);
  });
  it("the canvas label of a 1 V level carries into volts", () => {
    expect(formatEng(0.99994, "V", 3)).toBe("1 V");
    expect(formatEng(0.0009996, "V", 3)).toBe("1 mV");
  });
});

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
  it("6½-digit multimeter on a range: the range's prefix and width, no digit past the resolution (#1256)", () => {
    const dmm = (v: number, range: number, resolution?: number) => text(formatReadout(v, { unit: "V", digits: 7, fixed: true, range, resolution }));
    expect(dmm(-20e-9, 0.1)).toBe("+000.0000 mV"); // 20 nV is below the 0.1 µV display step
    expect(dmm(-2.0e-7, 0.1)).toBe("-000.0002 mV");
    expect(dmm(0.0123456789, 0.1)).toBe("+012.3457 mV");
    expect(dmm(0.5, 1)).toBe("+0.500000 V");
    expect(dmm(5, 10)).toBe("+05.00000 V");
    expect(dmm(999.9, 1000)).toBe("+0999.900 V"); // no kV on a meter
    expect(dmm(0.05, 0.1, 3e-7)).toBe("+050.000 mV"); // an A/D step of 0.3 µV: one digit fewer
    expect(text(formatReadout(1234.5, { unit: "Ω", digits: 7, fixed: true, range: 10000 }))).toBe("+01.23450 kΩ");
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

describe("displayUnit", () => {
  it("writes the SCPI units as people read them, dB in lower case (#1222)", () => {
    expect(displayUnit("DB")).toBe("dB");
    expect(displayUnit("DBM")).toBe("dBm");
    expect(displayUnit("PCT")).toBe("%");
    expect(displayUnit("RAD")).toBe("rad");
    expect(displayUnit("HZ")).toBe("Hz");
    expect(displayUnit("W")).toBe("W");
  });
});

describe("unitWord (#1257)", () => {
  it("reads the unit typed after a number, without its prefix", () => {
    expect(unitWord("2 Vrms")).toBe("vrms");
    expect(unitWord("1.5 kHz")).toBe("hz");
    expect(unitWord("20 m")).toBe("");
    expect(unitWord("3 dBm")).toBe("dbm");
    expect(unitWord("abc")).toBeNull();
  });
});

describe("makerName (#1356)", () => {
  it("drops the company word and writes shouted names as names", () => {
    expect(makerName("Keysight Technologies")).toBe("Keysight");
    expect(makerName("RIGOL TECHNOLOGIES")).toBe("Rigol");
    expect(makerName("FLUKE")).toBe("Fluke");
  });
  it("keeps initialisms and mixed-case names", () => {
    expect(makerName("B&K Precision")).toBe("B&K Precision");
    expect(makerName("Teledyne LeCroy")).toBe("Teledyne LeCroy");
    expect(makerName("Rohde & Schwarz")).toBe("Rohde & Schwarz");
  });
});
