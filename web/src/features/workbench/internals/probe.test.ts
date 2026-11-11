import { describe, expect, it } from "vitest";
import { ApiError } from "@/api/client";
import i18n, { type T } from "@/i18n";
import fixture from "./internals.fixture.json";
import {
  type InternalProbe,
  flowTag,
  fullScaleText,
  lineStems,
  probeable,
  spectrumBars,
  stairs,
  strongestBins,
  withheldError,
} from "./probe";

const zh = i18n.getFixedT("zh") as T;
const en = i18n.getFixedT("en") as T;
// Real probe answers of the SDS2104X Plus (scripts/capture_internals_fixture.py).
const probes = fixture.probes as unknown as Record<string, InternalProbe>;

describe("flowTag (signal-chain.md 1.2)", () => {
  it("labels an analog wire as analog, in words", () => {
    const analog = { medium: "signal" as const, sample_rate_hz: null, format: null };
    expect(flowTag(zh, analog)).toBe("模拟");
    expect(flowTag(en, analog)).toBe("Analog");
  });

  it("labels a sample stream with its rate and the converter's resolution", () => {
    const adc = { medium: "samples" as const, sample_rate_hz: 2e9, format: { kind: "volts" as const, bits: 8 } };
    expect(flowTag(zh, adc)).toBe("数字 · 2 GSa/s · 8 位");
    expect(flowTag(en, adc)).toBe("Digital · 2 GSa/s · 8-bit");
  });

  it("names IQ pairs, frequency bins and unquantized streams, and leaves out a rate it does not know", () => {
    expect(flowTag(zh, { medium: "samples", sample_rate_hz: 50e6, format: { kind: "iq", bits: 16 } })).toBe("数字 · 50 MSa/s · IQ");
    expect(flowTag(zh, { medium: "samples", sample_rate_hz: null, format: { kind: "bins", bits: 0 } })).toBe("数字 · 频点");
    expect(flowTag(zh, { medium: "samples", sample_rate_hz: 1e6, format: { kind: "volts", bits: 0 } })).toBe("数字 · 1 MSa/s · 未量化");
  });
});

describe("probeable", () => {
  it("opens on signal and sample-stream outputs only", () => {
    expect(probeable({ direction: "out", medium: "signal" })).toBe(true);
    expect(probeable({ direction: "out", medium: "samples" })).toBe(true);
    expect(probeable({ direction: "in", medium: "samples" })).toBe(false);
    expect(probeable({ direction: "out", medium: "electrical" })).toBe(false);
    expect(probeable({ direction: "out", medium: "reference" })).toBe(false);
  });
});

describe("fullScaleText", () => {
  it("says how much of the converter's range the frame uses", () => {
    expect(fullScaleText(zh, 0.37)).toBe("用到满量程的 37%");
    expect(fullScaleText(en, 0.37)).toBe("Uses 37% of full scale");
    expect(fullScaleText(zh, probes["adc.out1"].full_scale_used)).toBe("用到满量程的 2%");
    expect(fullScaleText(zh, 0.0042)).toBe("用到满量程的 0.42%");
    expect(fullScaleText(zh, null)).toBeNull();
  });
});

describe("stairs", () => {
  it("draws each sample as a step, never a slope between samples", () => {
    const { d, lo, hi } = stairs([0, 1, 1, 3], 40, 20, 0);
    expect([lo, hi]).toEqual([0, 3]);
    // only horizontal (H) and vertical (V) moves after the start: no interpolation
    expect(d).toBe("M0,20.0H10.0V13.3H20.0V13.3H30.0V0.0H40.0");
  });

  it("scales to the samples' own range, so a frame using 2 % of full scale still shows its codes", () => {
    const volts = probes["adc.out1"].waveform.volts;
    const { lo, hi } = stairs(volts, 280, 72);
    expect(lo).toBe(Math.min(...volts));
    expect(hi).toBe(Math.max(...volts));
    expect(hi).toBeGreaterThan(lo);
  });

  it("draws a constant frame through the middle", () => {
    expect(stairs([2, 2], 10, 20).d).toBe("M0,10.0H5.0V10.0H10.0");
  });
});

describe("spectrumBars and strongestBins", () => {
  it("keeps the strongest bin of a pixel column, so a peak is never hidden", () => {
    const level = Array.from({ length: 1000 }, () => -150);
    level[503] = -10;
    const { d, hi, lo } = spectrumBars(level, 100, 50, 0);
    expect([hi, lo]).toEqual([-10, -110]);
    expect(d.split("M")).toHaveLength(101); // one bar per column
    expect(d).toContain("M50.5,50.0V0.0"); // the column holding bin 503 reaches the top
  });

  it("finds the frame's strongest bins, DC left out", () => {
    const spectrum = probes["adc.out1"].spectrum!;
    const top = strongestBins(spectrum, 3);
    expect(top).toHaveLength(3);
    expect(top[0].level).toBe(Math.max(...spectrum.level.slice(1)));
    expect(top.map((b) => b.freq)).not.toContain(0);
  });
});

describe("lineStems", () => {
  it("draws an analog port's lines on a dB scale from the strongest down", () => {
    const { d, hi, top } = lineStems([{ freq_hz: 1000, amplitude: 1 }, { freq_hz: 3000, amplitude: 0.01 }], 330, 50, 0);
    expect(hi).toBe(0);
    expect(top).toBeCloseTo(3300, 9);
    // 1 kHz at the top, 3 kHz 40 dB (of 100) lower
    expect(d).toBe("M100.0,50.0V0.0M300.0,50.0V20.0");
  });
});

describe("withheldError", () => {
  it("tells validation mode's 403 from a failure", () => {
    expect(withheldError(new ApiError(403, "withheld"))).toBe(true);
    expect(withheldError(new ApiError(404, "no port"))).toBe(false);
    expect(withheldError(new Error("network"))).toBe(false);
  });
});
