import { describe, expect, it } from "vitest";
import { ApiError } from "@/api/client";
import i18n, { type T } from "@/i18n";
import fixture from "./internals.fixture.json";
import specan from "./specan.fixture.json";
import {
  CENTER,
  CLOSE_UP,
  IQ_SAMPLES,
  type InternalProbe,
  type InternalTuning,
  type MiniSignal,
  type Tune,
  binAt,
  binOf,
  binX,
  codeLines,
  dbc,
  formatTune,
  freqAt,
  inTuningRange,
  iqStairs,
  lineWindow,
  nearestLine,
  noiseFloor,
  onRadioAxis,
  parseTune,
  spectrumAxis,
  spectrumColumns,
  tuneHz,
  tuneParam,
  xOf,
  envelope,
  flowTag,
  fullScaleText,
  lineStems,
  lsbOf,
  miniOf,
  miniPath,
  probeable,
  spectrumBars,
  stairs,
  strongestPeaks,
  withheldError,
} from "./probe";

const zh = i18n.getFixedT("zh") as T;
const en = i18n.getFixedT("en") as T;
// Real probe answers of the SDS2104X Plus (scripts/capture_internals_fixture.py).
const probes = fixture.probes as unknown as Record<string, InternalProbe>;
// The digital-IF analyzer with two tones 1 MHz apart (web/e2e/fixtures/benches/sa-two-tone, #959).
const sa = specan.probes as unknown as Record<string, InternalProbe>;

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

  it("finds the frame's strongest peaks, DC left out", () => {
    const spectrum = probes["adc.out1"].spectrum!;
    const top = strongestPeaks(spectrum, 3);
    expect(top).toHaveLength(3);
    expect(top[0].level).toBe(Math.max(...spectrum.level.slice(1)));
    expect(top.map((b) => b.freq)).not.toContain(0);
  });

  it("lists a windowed tone once, not once per bin it leaks into, so the next component shows", () => {
    // Two tones, each spread over its neighbours by the window; the second is 30 dB down.
    const level = Array.from({ length: 64 }, () => -90);
    for (const [bin, top] of [[10, -6], [40, -36]] as const) {
      level[bin] = top;
      level[bin - 1] = level[bin + 1] = top - 6;
    }
    const peaks = strongestPeaks({ freq_hz: level.map((_, i) => i * 1000), level, unit: "dBFS" }, 2);
    expect(peaks.map((p) => p.freq)).toEqual([10_000, 40_000]);
  });
});

describe("envelope", () => {
  it("keeps every extreme of a frame longer than the picture is wide", () => {
    const values = Array.from({ length: 4096 }, () => 0);
    values[1234] = 1; // one sample, between pixel columns' centres
    values[3000] = -1;
    const plot = envelope(values, 280, 88);
    expect([plot.lo, plot.hi]).toEqual([-1, 1]);
    // The single high sample reaches the top of the picture, the low one the bottom.
    const strokes = plot.d.split("M").slice(1).map((s) => s.split(/* x,bottom V top */ "V").map((p) => Number(p.split(",").pop())));
    expect(strokes).toHaveLength(280);
    expect(Math.min(...strokes.map(([, top]) => top))).toBe(4);
    expect(Math.max(...strokes.map(([bottom]) => bottom))).toBe(84);
  });
});

describe("lsbOf and codeLines", () => {
  it("derives one code from the frame's span and the share of full scale it uses", () => {
    const adc = probes["adc.out1"];
    const lsb = lsbOf(adc)!;
    const volts = adc.waveform.volts;
    const codes = (Math.max(...volts) - Math.min(...volts)) / lsb;
    // The frame spans a whole number of codes, and full scale is 2^bits of them.
    expect(codes).toBeCloseTo(Math.round(codes), 6);
    expect(codes / 2 ** adc.format!.bits).toBeCloseTo(adc.full_scale_used!, 9);
  });

  it("has no code size for an analog port or a flat frame", () => {
    expect(lsbOf(probes["ch1.front.out"])).toBeNull();
    expect(lsbOf({ format: { kind: "volts", bits: 8 }, full_scale_used: 0, waveform: { volts: [0, 0, 0] } })).toBeNull();
  });

  it("draws a line per code when they are few, none when they would be a grey field", () => {
    expect(codeLines(0, 0.05, 0.01, 88)).toHaveLength(6);
    expect(codeLines(0, 0.05, 0.01, 88)[0]).toBe(84);
    expect(codeLines(0, 1, 0.001, 88)).toEqual([]);
    expect(codeLines(0, 1, null, 88)).toEqual([]);
  });
});

describe("miniOf (the thumbnail on a block's output)", () => {
  const thumbs = fixture.signals as unknown as Record<string, MiniSignal>;

  it("thins an analytic waveform and keeps a stream's first samples as they are", () => {
    const analog = miniOf(probes["ch1.front.out"]);
    expect(analog.kind).toBe("analog");
    expect(analog.values.length).toBeLessThanOrEqual(100);
    const stream = miniOf(probes["adc.out1"]);
    expect(stream).toMatchObject({ kind: "samples", silent: false });
    expect(stream.values).toEqual(probes["adc.out1"].waveform.volts.slice(0, stream.values.length));
    expect(stream.values.length).toBeLessThan(CLOSE_UP);
  });

  it("matches the thumbnails the fixture was captured with (scripts/capture_internals_fixture.py)", () => {
    for (const port of Object.keys(probes)) expect(miniOf(probes[port]), port).toEqual(thumbs[port]);
  });

  it("draws steps for samples, a line for an analog signal, and nothing for a port that reads 0", () => {
    const stream = miniPath(thumbs["adc.out1"], 110, 22);
    expect(stream.includes("H") && stream.includes("V") && !stream.includes("L")).toBe(true);
    const analog = miniPath(thumbs["ch1.front.out"], 110, 22);
    expect(analog.includes("L") && !analog.includes("H")).toBe(true);
    expect(thumbs["ch2.front.out"].silent).toBe(true);
    expect(miniPath(thumbs["ch2.front.out"], 110, 22)).toBe("");
  });
});

describe("lineStems and lineWindow", () => {
  it("draws an analog port's lines on a dB scale from the strongest down", () => {
    const lines = [{ freq_hz: 1000, amplitude: 1 }, { freq_hz: 3000, amplitude: 0.01 }];
    // a fundamental and its harmonic read from 0 Hz: nothing to zoom on
    const axis = lineWindow(lines);
    expect(axis.lo).toBe(0);
    expect(axis.hi).toBeCloseTo(3300, 9);
    expect([axis.zoomable, axis.outside]).toEqual([false, 0]);
    const { d, hi } = lineStems(lines, 330, 50, axis, 0);
    expect(hi).toBe(0);
    // 1 kHz at the top, 3 kHz 40 dB (of 100) lower
    expect(d).toBe("M100.0,50.0V0.0M300.0,50.0V20.0");
  });

  it("gives two tones 1 MHz apart at 1 GHz an axis of their own, and 0 Hz back on request", () => {
    const tones = [{ freq_hz: 1.0e9, amplitude: 0.1 }, { freq_hz: 1.001e9, amplitude: 0.1 }];
    const near = lineWindow(tones);
    expect([near.lo, near.hi]).toEqual([1.0e9 - 0.25e6, 1.001e9 + 0.25e6]);
    expect([near.zoomable, near.outside]).toEqual([true, 0]);
    expect(xOf(1.001e9, near, 300) - xOf(1.0e9, near, 300)).toBeCloseTo(200, 6);
    const whole = lineWindow(tones, true);
    expect([whole.lo, whole.zoomable]).toEqual([0, true]);
    expect(xOf(1.001e9, whole, 300) - xOf(1.0e9, whole, 300)).toBeLessThan(1);
  });

  it("shows the mixer's IF group — the tones and their third-order products — and counts the sum products left outside (#959)", () => {
    const lines = sa["mixer.out"].lines;
    const axis = lineWindow(lines);
    const inside = lines.filter((l) => l.freq_hz >= axis.lo && l.freq_hz <= axis.hi).map((l) => l.freq_hz);
    expect(inside.sort((a, b) => a - b)).toEqual([23.5e6, 24.5e6, 25.5e6, 26.5e6]);
    expect(axis.outside).toBe(lines.length - 4);
    expect(axis.zoomable).toBe(true);
    // the products stand 60 dB under the tones: −20 dBm into a +10 dBm intercept
    const product = lines.find((l) => l.freq_hz === 23.5e6)!;
    expect(dbc(product, lines)).toBeCloseTo(-60, 0);
    expect(dbc(lines[0], lines)).toBe(0);
    expect(nearestLine(lines, 23.6e6, axis)).toBe(product);
    expect(nearestLine(lines, 2.0e9, axis)?.freq_hz).toBe(26.5e6); // only the lines on the axis are read
  });

  it("keeps DC with what stands above it, and leaves a single line where it was", () => {
    const withDc = lineWindow([{ freq_hz: 0, amplitude: 1 }, { freq_hz: 1000, amplitude: 0.5 }]);
    expect([withDc.lo, withDc.outside, withDc.zoomable]).toEqual([0, 0, false]);
    expect(withDc.hi).toBeCloseTo(1100, 9);
    const one = lineWindow([{ freq_hz: 25e6, amplitude: 1 }]);
    expect([one.lo, one.zoomable]).toEqual([0, false]);
    expect(lineWindow([]).zoomable).toBe(false);
  });
});

describe("a frame's spectrum (signal-chain.md 1.3, #959)", () => {
  const ddc = sa["ddc.out"];
  const rbw = sa["rbw.out"];
  const adc = sa["adc.out"];

  it("runs an IQ stream's and a transform's bins over the radio frequencies around the tuning, a real stream's over 0 to half its rate", () => {
    expect(onRadioAxis(ddc)).toBe(true);
    expect(onRadioAxis(rbw)).toBe(true);
    expect(onRadioAxis(adc)).toBe(false);
    const axis = spectrumAxis(ddc.spectrum!);
    expect(axis.lo).toBeLessThan(ddc.tune_hz!);
    expect(axis.hi).toBeGreaterThan(ddc.tune_hz!);
    expect((axis.lo + axis.hi) / 2).toBeCloseTo(ddc.tune_hz!, -4); // the tuning in the middle (to a bin)
    expect(spectrumAxis(adc.spectrum!)).toEqual({ lo: 0, hi: 50e6 });
    expect(spectrumAxis({ freq_hz: [] })).toEqual({ lo: 0, hi: 1 });
  });

  it("reads the strongest bin of the bar under the pointer, so a tone is read at its peak", () => {
    const spectrum = rbw.spectrum!;
    const tone = binOf(spectrum, 1.0e9);
    const x = binX(tone, spectrum.level.length, 280);
    const read = binAt(spectrum, x, 280);
    expect(Math.abs(spectrum.freq_hz[read] - 1.0e9)).toBeLessThan(2 * (spectrum.freq_hz[1] - spectrum.freq_hz[0]));
    expect(spectrum.level[read]).toBeGreaterThan(noiseFloor(spectrum)! + 60);
    // the ends of the picture read the ends of the spectrum
    expect(binAt(spectrum, -5, 280)).toBeLessThan(spectrum.level.length / spectrumColumns(spectrum.level.length, 280));
    expect(binAt(spectrum, 9999, 280)).toBeGreaterThanOrEqual(spectrum.level.length - Math.ceil(spectrum.level.length / 280) - 1);
    expect(binAt({ ...spectrum, level: [], freq_hz: [] }, 10, 280)).toBe(-1);
  });

  it("takes the median level as the noise floor: tones do not move it", () => {
    expect(noiseFloor({ level: [-100, -101, -99, -20, -100] })).toBe(-100);
    expect(noiseFloor({ level: [-100, -90] })).toBe(-95);
    expect(noiseFloor({ level: [] })).toBeNull();
    // the ADC's own floor: far under the tones, which stand near −21 dBFS
    expect(noiseFloor(adc.spectrum!)!).toBeLessThan(-90);
  });

  it("finds both tones in the transform's bins, 1 MHz apart", () => {
    const [a, b] = strongestPeaks(rbw.spectrum!, 2).map((p) => p.freq).sort((x, y) => x - y);
    expect(a).toBeCloseTo(1.0e9, -4);
    expect(b - a).toBeCloseTo(1.0e6, -4);
  });

  it("draws I and Q as two staircases on one scale", () => {
    const { i, q, lo, hi, count } = iqStairs([1, -1, 1], [0.5, 0.5, -3], 30, 20, 96, 0);
    expect(count).toBe(3);
    expect([lo, hi]).toEqual([-3, 1]);
    expect(i).toBe("M0,0.0H10.0V10.0H20.0V0.0H30.0");
    expect(q).toBe("M0,2.5H10.0V2.5H20.0V20.0H30.0");
    expect(iqStairs(ddc.waveform.volts, ddc.quadrature!, 280, 88).count).toBe(IQ_SAMPLES);
    expect(iqStairs([], [], 280, 88).i).toBe("");
  });
});

describe("tuned to (signal-chain.md 1.3, #959)", () => {
  const tuning = specan.internals.tuning as unknown as InternalTuning;
  const marker: Tune = { kind: "mark", mark: "marker", index: 1 };

  it("knows the analyzer's tuning context from the engine: center, range, the marker, the ports after the mixer", () => {
    expect(tuning.center_hz).toBe(1.0005e9);
    expect([tuning.min_hz, tuning.max_hz]).toEqual([0, 7.5e9]);
    expect(tuning.marks).toEqual([{ kind: "marker", index: 1, freq_hz: 1.0e9 }]);
    expect(tuning.ports).toEqual(["adc.out", "ddc.out", "if_filter.out", "mixer.out", "rbw.out"]);
  });

  it("stands for the center, a marker or a typed frequency", () => {
    expect(tuneHz(CENTER, tuning)).toBe(1.0005e9);
    expect(tuneHz(marker, tuning)).toBe(1.0e9);
    expect(tuneHz({ kind: "manual", hz: 2e9 }, tuning)).toBe(2e9);
    // a marker turned off: back to the center
    expect(tuneHz(marker, { ...tuning, marks: [] })).toBe(1.0005e9);
  });

  it("asks the service for a tuning only where the port depends on it, and never for the center", () => {
    expect(tuneParam(CENTER, tuning, "mixer.out")).toBeUndefined();
    expect(tuneParam(marker, tuning, "mixer.out")).toBe(1.0e9);
    expect(tuneParam(marker, tuning, "input.out")).toBeUndefined(); // before the mixer
    expect(tuneParam(marker, { ...tuning, marks: [] }, "mixer.out")).toBeUndefined();
    expect(tuneParam({ kind: "manual", hz: 2e9 }, tuning, "rbw.out")).toBe(2e9);
    expect(tuneParam(marker, null, "adc.out1")).toBeUndefined(); // an oscilloscope
  });

  it("reads a typed frequency with a prefix and a unit, and refuses what the instrument cannot tune to", () => {
    expect(parseTune("1.0005G", tuning)).toBe(1.0005e9);
    expect(parseTune("999.5 MHz", tuning)).toBe(999.5e6);
    expect(parseTune("1e9", tuning)).toBe(1e9);
    expect(parseTune("1000 Hz", tuning)).toBe(1000);
    expect(parseTune("8 GHz", tuning)).toBe("range");
    expect(parseTune("-1", tuning)).toBe("range");
    expect(parseTune("inf", tuning)).toBeNull();
    expect(parseTune("fast", tuning)).toBeNull();
    expect(parseTune("  ", tuning)).toBeNull();
    expect(inTuningRange(5e9, { min_hz: null, max_hz: null })).toBe(true);
  });

  it("writes a tuning with the digits that tell 1.0005 GHz from 1 GHz", () => {
    expect(formatTune(1.0005e9)).toBe("1.0005 GHz");
    expect(formatTune(1.0e9)).toBe("1 GHz");
  });

  it("reads the probes at the tuning asked for: the marker's tone lands on 0 Hz of the IQ stream", () => {
    expect(sa["ddc.out"].tune_hz).toBe(1.0005e9);
    expect(sa["ddc.out@1e9"].tune_hz).toBe(1.0e9);
    const axis = spectrumAxis(sa["ddc.out@1e9"].spectrum!);
    expect(freqAt(xOf(1.0e9, axis, 280), axis, 280)).toBeCloseTo(1.0e9, -2);
    expect(xOf(1.0e9, axis, 280)).toBeCloseTo(140, 0);
  });
});

describe("withheldError", () => {
  it("tells validation mode's 403 from a failure", () => {
    expect(withheldError(new ApiError(403, "withheld"))).toBe(true);
    expect(withheldError(new ApiError(404, "no port"))).toBe(false);
    expect(withheldError(new Error("network"))).toBe(false);
  });
});
