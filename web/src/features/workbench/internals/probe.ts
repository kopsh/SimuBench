import type { Schemas } from "@/api/client";
import type { T } from "@/i18n";
import { formatEng, parseEngineering } from "@/lib/format";
import type { GraphEdge } from "./layout";

/**
 * The internal probe's pure parts (signal-chain.md 1.2, 1.3, #953): what a wire is labelled with,
 * which ports open a probe, and the pictures of a probed signal. Unit-tested in probe.test.ts.
 */

export type InternalProbe = Schemas["InternalProbe"];
export type InternalTuning = Schemas["InternalTuning"];
export type Spectrum = NonNullable<InternalProbe["spectrum"]>;
export type Flow = NonNullable<GraphEdge["flow"]>;
type Format = NonNullable<Flow["format"]>;

/** Media a probe reads: analog (analytic) signals and sample streams. */
const PROBED = new Set(["signal", "samples"]);

/** An output port the internal probe can read: a signal or sample-stream output. */
export function probeable(port: { direction: string; medium: string }): boolean {
  return port.direction === "out" && PROBED.has(port.medium);
}

/** A sample rate the way an instrument data sheet writes it: 2 GSa/s, 50 MSa/s. */
export function formatRate(hz: number): string {
  return formatEng(hz, "Sa/s", 4);
}

/** A stream's format: the converter's resolution, IQ pairs, frequency bins, or unquantized volts. */
export function formatFormat(t: T, format: Format): string {
  if (format.kind === "iq") return "IQ";
  if (format.kind === "bins") return t("internals.flow.bins");
  if (format.bits > 0) return t("internals.flow.bits", { bits: format.bits });
  return t("internals.flow.unquantized");
}

/**
 * The label on a wire (signal-chain.md 1.2): analog, or digital with the stream's rate and format
 * with the current settings (`数字 · 2 GSa/s · 8 位`). Text, not colour alone.
 */
export function flowTag(t: T, flow: Pick<Flow, "medium" | "sample_rate_hz" | "format">): string {
  if (flow.medium !== "samples") return t("internals.flow.analog");
  const parts: string[] = [t("internals.flow.digital")];
  if (flow.sample_rate_hz) parts.push(formatRate(flow.sample_rate_hz));
  if (flow.format) parts.push(formatFormat(t, flow.format));
  return parts.join(" · ");
}

/** How much of the converter's range the frame spans (`用到满量程的 37%`); none when unquantized. */
export function fullScaleText(t: T, fraction: number | null | undefined): string | null {
  if (fraction == null) return null;
  const pct = fraction * 100;
  return t("internals.probe.fullScale", { pct: pct >= 1 || pct === 0 ? Math.round(pct) : Number(pct.toPrecision(2)) });
}

export type Plot = { d: string; lo: number; hi: number };

/**
 * Samples as a staircase, one step per sample, never joined by a slope (no interpolation: the
 * quantization steps stay visible). The vertical range is the samples' own, so an 8-bit frame
 * that uses 2 % of full scale still shows its codes; a constant frame is a line through the middle.
 */
export function stairs(values: readonly number[], width: number, height: number, pad = 4): Plot {
  if (!values.length) return { d: "", lo: 0, hi: 0 };
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = hi - lo;
  const y = (v: number) => (span ? height - pad - ((v - lo) / span) * (height - 2 * pad) : height / 2);
  const dx = width / values.length;
  const parts: string[] = [`M0,${y(values[0]).toFixed(1)}`];
  for (let i = 0; i < values.length; i++) {
    const yi = y(values[i]).toFixed(1);
    if (i > 0) parts.push(`V${yi}`);
    parts.push(`H${((i + 1) * dx).toFixed(1)}`);
  }
  return { d: parts.join(""), lo, hi };
}

/** A line through analytic samples (an analog port's waveform, evaluated exactly at each point). */
export function trace(values: readonly number[], width: number, height: number, pad = 4): Plot {
  if (!values.length) return { d: "", lo: 0, hi: 0 };
  const lo = Math.min(0, ...values);
  const hi = Math.max(0, ...values);
  const span = hi - lo || 1;
  const y = (v: number) => height - pad - ((v - lo) / span) * (height - 2 * pad);
  const last = Math.max(values.length - 1, 1);
  const d = values.map((v, i) => `${i ? "L" : "M"}${((i / last) * width).toFixed(1)},${y(v).toFixed(1)}`).join("");
  return { d, lo, hi };
}

export type Line = { freq_hz: number; amplitude: number };
/** The frequencies a picture's horizontal axis runs over, linear. */
export type Axis = { lo: number; hi: number };

/** Lines a decade or more apart, with most of the axis empty between them, are two groups. */
const GROUP_RATIO = 10;
const GROUP_GAP = 0.5;

/**
 * The axis of an analog port's line picture. From 0 Hz to a little past the highest line where
 * that shows them apart (a fundamental and its harmonics). Lines that crowd far from 0 — two
 * tones 1 MHz apart at 1 GHz; a mixer's IF products at 25 MHz beside its sum products at 2 GHz —
 * would fall on one pixel of such an axis: the picture is then the group around the strongest
 * line, and `outside` counts the lines left out of it (`whole` puts them back, from 0 Hz; `zoomable` says there is such a choice).
 */
export function lineWindow(lines: readonly Line[], whole = false): Axis & { outside: number; zoomable: boolean } {
  const shown = lines.filter((l) => l.amplitude > 0);
  if (!shown.length) return { lo: 0, hi: 1, outside: 0, zoomable: false };
  const all = { lo: 0, hi: Math.max(...shown.map((l) => l.freq_hz)) * 1.1 || 1 };
  const strongest = shown.reduce((a, b) => (b.amplitude > a.amplitude ? b : a));
  let group = [...shown].sort((a, b) => a.freq_hz - b.freq_hz);
  // Cut at the widest gap while it is a real divide; keep the side the strongest line is on.
  for (;;) {
    let at = -1;
    let widest = 0;
    for (let i = 1; i < group.length; i++) {
      const gap = group[i].freq_hz - group[i - 1].freq_hz;
      if (gap > widest) [widest, at] = [gap, i];
    }
    const span = group[group.length - 1].freq_hz - group[0].freq_hz;
    // (DC stands with whatever is above it: 0 Hz is no decade away from anything.)
    if (at < 0 || widest <= GROUP_GAP * span || group[at - 1].freq_hz <= 0 || group[at].freq_hz < GROUP_RATIO * group[at - 1].freq_hz) break;
    group = strongest.freq_hz >= group[at].freq_hz ? group.slice(at) : group.slice(0, at);
  }
  const lo = group[0].freq_hz;
  const hi = group[group.length - 1].freq_hz;
  // A group that starts near 0 reads best from 0; one line alone has no span to zoom on.
  const crowded = hi > lo && lo > 0.5 * hi;
  const pad = (hi - lo) * 0.25;
  const near = crowded ? { lo: Math.max(0, lo - pad), hi: hi + pad } : { lo: 0, hi: hi * 1.1 || 1 };
  const outside = shown.filter((l) => l.freq_hz < near.lo || l.freq_hz > near.hi).length;
  const zoomable = crowded || outside > 0;
  return whole || !zoomable ? { ...all, outside: 0, zoomable } : { ...near, outside, zoomable };
}

/** Where a frequency stands on a picture `width` wide. */
export function xOf(freq: number, axis: Axis, width: number): number {
  return axis.hi > axis.lo ? ((freq - axis.lo) / (axis.hi - axis.lo)) * width : width / 2;
}

/** The frequency under a point of the picture. */
export function freqAt(x: number, axis: Axis, width: number): number {
  return axis.lo + (Math.min(Math.max(x, 0), width) / width) * (axis.hi - axis.lo);
}

/**
 * An analog port's lines as stems on a dB scale (the strongest at the top, `SPECTRUM_RANGE_DB`
 * below it at the bottom), on `axis` (`lineWindow`); lines outside it are not drawn.
 */
export function lineStems(lines: readonly Line[], width: number, height: number, axis: Axis = lineWindow(lines, true), pad = 4): Plot {
  const shown = lines.filter((l) => l.amplitude > 0);
  if (!shown.length) return { d: "", lo: 0, hi: 0 };
  const hi = Math.max(...shown.map((l) => 20 * Math.log10(l.amplitude)));
  const lo = hi - SPECTRUM_RANGE_DB;
  const d = shown
    .filter((l) => l.freq_hz >= axis.lo && l.freq_hz <= axis.hi)
    .map((l) => `M${xOf(l.freq_hz, axis, width).toFixed(1)},${(height - pad).toFixed(1)}V${levelY(20 * Math.log10(l.amplitude), lo, height, pad).toFixed(1)}`)
    .join("");
  return { d, lo, hi };
}

/** A level's height in a picture whose bottom is `lo` and whose top is `SPECTRUM_RANGE_DB` above it. */
export function levelY(level: number, lo: number, height: number, pad = 4): number {
  return height - pad - ((Math.min(Math.max(level, lo), lo + SPECTRUM_RANGE_DB) - lo) / SPECTRUM_RANGE_DB) * (height - 2 * pad);
}

/** A line's level under the strongest, dBc (0 for the strongest itself). */
export function dbc(line: Line, lines: readonly Line[]): number {
  const top = Math.max(...lines.map((l) => l.amplitude));
  return top > 0 && line.amplitude > 0 ? 20 * Math.log10(line.amplitude / top) : -Infinity;
}

/** The line nearest a frequency among those on the axis; none when the axis holds no line. */
export function nearestLine<L extends Line>(lines: readonly L[], freq: number, axis: Axis): L | null {
  let best: L | null = null;
  for (const l of lines) {
    if (l.amplitude <= 0 || l.freq_hz < axis.lo || l.freq_hz > axis.hi) continue;
    if (!best || Math.abs(l.freq_hz - freq) < Math.abs(best.freq_hz - freq)) best = l;
  }
  return best;
}

/** How many dB of the spectrum the picture shows below its strongest point. */
export const SPECTRUM_RANGE_DB = 100;

/**
 * A frame's amplitude spectrum as bars from the bottom (a spectrum of a finite frame has a value at
 * every bin; a line between bins would suggest values that are not there). Linear frequency axis
 * over the bins as they come — 0 to half the rate for real samples, the radio frequencies around
 * the tuning for IQ samples and a transform's bins (`spectrumAxis`); the top is the strongest bin,
 * the bottom `SPECTRUM_RANGE_DB` below it.
 */
export function spectrumBars(level: readonly number[], width: number, height: number, pad = 4): Plot {
  if (!level.length) return { d: "", lo: 0, hi: 0 };
  const hi = Math.max(...level);
  const lo = hi - SPECTRUM_RANGE_DB;
  const y = (v: number) => height - pad - ((Math.max(v, lo) - lo) / SPECTRUM_RANGE_DB) * (height - 2 * pad);
  // One bar per pixel column at most: the strongest bin of the column (a peak is never hidden).
  const columns = Math.min(level.length, Math.floor(width));
  const parts: string[] = [];
  for (let c = 0; c < columns; c++) {
    const from = Math.floor((c * level.length) / columns);
    const to = Math.max(from + 1, Math.floor(((c + 1) * level.length) / columns));
    let top = -Infinity;
    for (let i = from; i < to; i++) top = Math.max(top, level[i]);
    const x = (((c + 0.5) * width) / columns).toFixed(1);
    parts.push(`M${x},${(height - pad).toFixed(1)}V${y(top).toFixed(1)}`);
  }
  return { d: parts.join(""), lo, hi };
}

/** The frequencies a frame's spectrum runs over: its first bin to its last. */
export function spectrumAxis(spectrum: Pick<Spectrum, "freq_hz">): Axis {
  const f = spectrum.freq_hz;
  return f.length ? { lo: f[0], hi: f[f.length - 1] } : { lo: 0, hi: 1 };
}

/** How many bars `spectrumBars` draws for a spectrum of `bins` points in a picture `width` wide. */
export function spectrumColumns(bins: number, width: number): number {
  return Math.min(bins, Math.floor(width));
}

/**
 * The bin a pointer at `x` reads: the strongest of the bar under it — what the bar shows — so a
 * tone is read at its peak, not at whichever of its neighbours the pointer happens to be over.
 */
export function binAt(spectrum: Spectrum, x: number, width: number): number {
  const n = spectrum.level.length;
  if (!n) return -1;
  const columns = spectrumColumns(n, width);
  const c = Math.min(columns - 1, Math.max(0, Math.floor((x / width) * columns)));
  const from = Math.floor((c * n) / columns);
  const to = Math.max(from + 1, Math.floor(((c + 1) * n) / columns));
  let best = from;
  for (let i = from; i < to; i++) if (spectrum.level[i] > spectrum.level[best]) best = i;
  return best;
}

/** The bar a bin is drawn in, as the x of its middle. */
export function binX(bin: number, bins: number, width: number): number {
  const columns = spectrumColumns(bins, width);
  return ((Math.min(columns - 1, Math.floor((bin * columns) / bins)) + 0.5) * width) / columns;
}

/** The bin nearest a frequency (the bins are evenly spaced). */
export function binOf(spectrum: Pick<Spectrum, "freq_hz">, freq: number): number {
  const f = spectrum.freq_hz;
  if (f.length < 2) return f.length - 1;
  return Math.min(f.length - 1, Math.max(0, Math.round(((freq - f[0]) / (f[f.length - 1] - f[0])) * (f.length - 1))));
}

/**
 * The noise floor of a frame's spectrum: the median level — half the bins lie under it, so a few
 * tones do not move it. What a converter's resolution or a stage's noise figure raises or lowers.
 */
export function noiseFloor(spectrum: Pick<Spectrum, "level">): number | null {
  if (!spectrum.level.length) return null;
  const sorted = [...spectrum.level].sort((a, b) => a - b);
  const mid = sorted.length >> 1;
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

/**
 * Whether a spectrum's axis is radio frequency: an IQ stream's and a transform's bins stand for the
 * frequencies around the tuning (signal-chain.md 1.3), so a point of the picture is a frequency the
 * receiver can be tuned to. A real stream's FFT runs over its own 0 to half the rate.
 */
export function onRadioAxis(probe: Pick<InternalProbe, "format" | "tune_hz">): boolean {
  return probe.tune_hz != null && (probe.format?.kind === "iq" || probe.format?.kind === "bins");
}

/**
 * Where the internal probe is tuned (signal-chain.md 1.3, "tuned to"): the center frequency (the
 * default — it follows the setting), a marker that is on (it follows the marker), or a frequency
 * typed in or picked on a spectrum.
 */
export type Tune = { kind: "center" } | { kind: "mark"; mark: "marker" | "reference"; index: number } | { kind: "manual"; hz: number };

export const CENTER: Tune = { kind: "center" };

/** The frequency a choice stands for now; a marker that has been turned off falls back to the center. */
export function tuneHz(tune: Tune, tuning: InternalTuning): number {
  if (tune.kind === "manual") return tune.hz;
  if (tune.kind === "mark") return tuning.marks?.find((m) => m.kind === tune.mark && m.index === tune.index)?.freq_hz ?? tuning.center_hz;
  return tuning.center_hz;
}

/** What goes to the service as `tune_hz`: nothing for the center (its default), so it keeps following it. */
export function tuneParam(tune: Tune, tuning: InternalTuning | null | undefined, port: string): number | undefined {
  if (!tuning || !tuning.ports?.includes(port)) return undefined;
  if (tune.kind === "center") return undefined;
  if (tune.kind === "mark" && !tuning.marks?.some((m) => m.kind === tune.mark && m.index === tune.index)) return undefined;
  return tuneHz(tune, tuning);
}

/** Whether a frequency is one the instrument can be tuned to. */
export function inTuningRange(hz: number, tuning: Pick<InternalTuning, "min_hz" | "max_hz">): boolean {
  return Number.isFinite(hz) && hz >= (tuning.min_hz ?? -Infinity) && hz <= (tuning.max_hz ?? Infinity);
}

/**
 * A typed tuning: a number with an SI prefix and an optional unit (`1.0005G`, `999.5 MHz`,
 * `1e9`). `null`: not a frequency; `"range"`: outside the instrument's frequency range.
 */
export function parseTune(text: string, tuning: Pick<InternalTuning, "min_hz" | "max_hz">): number | null | "range" {
  const typed = text.trim();
  if (!typed) return null;
  const unit = typed.slice(-2).toLowerCase() === "hz" ? typed.slice(0, -2) : typed;
  const hz = parseEngineering(unit);
  if (hz == null || !Number.isFinite(hz)) return null;
  return inTuningRange(hz, tuning) ? hz : "range";
}

/** A frequency on the scale of a tuning: enough digits to tell 1.0005 GHz from 1 GHz. */
export function formatTune(hz: number): string {
  return formatEng(hz, "Hz", 7);
}

/**
 * IQ samples as two staircases on one scale (in-phase and quadrature share the vertical range, so
 * their sizes compare): the first `count` samples — a frame is thousands, and two overlaid
 * envelopes of it would show neither.
 */
export function iqStairs(i: readonly number[], q: readonly number[], width: number, height: number, count = IQ_SAMPLES, pad = 4): { i: string; q: string; lo: number; hi: number; count: number } {
  const n = Math.min(count, i.length, q.length);
  if (!n) return { i: "", q: "", lo: 0, hi: 0, count: 0 };
  const a = i.slice(0, n);
  const b = q.slice(0, n);
  const lo = Math.min(...a, ...b);
  const hi = Math.max(...a, ...b);
  const span = hi - lo;
  const y = (v: number) => (span ? height - pad - ((v - lo) / span) * (height - 2 * pad) : height / 2);
  const dx = width / n;
  const path = (values: readonly number[]) => {
    const parts: string[] = [`M0,${y(values[0]).toFixed(1)}`];
    for (let k = 0; k < n; k++) {
      if (k > 0) parts.push(`V${y(values[k]).toFixed(1)}`);
      parts.push(`H${((k + 1) * dx).toFixed(1)}`);
    }
    return parts.join("");
  };
  return { i: path(a), q: path(b), lo, hi, count: n };
}

/** How many IQ samples the time picture shows: each step still a few pixels wide. */
export const IQ_SAMPLES = 96;

/**
 * The strongest peaks of a frame's spectrum, DC left out, for the table under the picture: local
 * maxima only, and no two closer than `apart` bins — a windowed tone spreads over neighbouring
 * bins, and listing those would hide the next real component.
 */
export function strongestPeaks(spectrum: NonNullable<InternalProbe["spectrum"]>, count = 4, apart = 3): { freq: number; level: number; bin: number }[] {
  const { level, freq_hz } = spectrum;
  const peaks: { freq: number; level: number; bin: number }[] = [];
  for (let i = 1; i < level.length; i++) {
    if (level[i] >= (level[i - 1] ?? -Infinity) && level[i] >= (level[i + 1] ?? -Infinity)) peaks.push({ freq: freq_hz[i], level: level[i], bin: i });
  }
  peaks.sort((x, y) => y.level - x.level || x.bin - y.bin);
  const kept: typeof peaks = [];
  for (const p of peaks) {
    if (kept.length === count) break;
    if (kept.every((k) => Math.abs(k.bin - p.bin) >= apart)) kept.push(p);
  }
  return kept;
}

/**
 * A long frame at a glance: per pixel column, a vertical stroke from the column's lowest sample to
 * its highest. With more samples than pixels a staircase would be a smear that shows neither the
 * steps nor the extremes; the envelope keeps every extreme (a single clipped sample still shows).
 */
export function envelope(values: readonly number[], width: number, height: number, pad = 4): Plot {
  if (!values.length) return { d: "", lo: 0, hi: 0 };
  let lo = Infinity;
  let hi = -Infinity;
  for (const v of values) {
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  const span = hi - lo;
  const y = (v: number) => (span ? height - pad - ((v - lo) / span) * (height - 2 * pad) : height / 2);
  const columns = Math.min(values.length, Math.floor(width));
  const parts: string[] = [];
  for (let c = 0; c < columns; c++) {
    const from = Math.floor((c * values.length) / columns);
    const to = Math.max(from + 1, Math.floor(((c + 1) * values.length) / columns));
    let a = Infinity;
    let b = -Infinity;
    for (let i = from; i < to; i++) {
      if (values[i] < a) a = values[i];
      if (values[i] > b) b = values[i];
    }
    const x = (((c + 0.5) * width) / columns).toFixed(1);
    // A column whose samples agree still gets a visible mark.
    const top = y(b);
    const bottom = Math.max(y(a), top + 1);
    parts.push(`M${x},${bottom.toFixed(1)}V${top.toFixed(1)}`);
  }
  return { d: parts.join(""), lo, hi };
}

/** How many samples the close-up shows: few enough that each step is several pixels wide. */
export const CLOSE_UP = 64;

/**
 * One code of the converter, in the stream's unit: the frame's span over the share of full scale it
 * uses gives full scale, and the resolution divides it. None when the stream is not quantized, or
 * the frame is flat (no span to measure with).
 */
export function lsbOf(probe: Pick<InternalProbe, "format" | "full_scale_used"> & { waveform: { volts: readonly number[] } }): number | null {
  const bits = probe.format?.bits ?? 0;
  const used = probe.full_scale_used ?? 0;
  if (bits <= 0 || used <= 0) return null;
  const { lo, hi } = envelope(probe.waveform.volts, 1, 1);
  return hi > lo ? (hi - lo) / used / 2 ** bits : null;
}

/** The code levels between `lo` and `hi` (their y in a plot of that range), when few enough to draw. */
export function codeLines(lo: number, hi: number, lsb: number | null, height: number, pad = 4, most = 32): number[] {
  if (!lsb || hi <= lo) return [];
  const codes = Math.round((hi - lo) / lsb);
  if (codes < 1 || codes > most) return [];
  return Array.from({ length: codes + 1 }, (_, i) => height - pad - (i / codes) * (height - 2 * pad));
}

/** The signal at a port, small enough to draw on its block: what the diagram shows without a click. */
export type MiniSignal = { kind: "analog" | "samples"; values: number[]; silent: boolean };

/** How many points a thumbnail draws: an analytic waveform thinned, or a stream's first samples. */
const MINI_ANALOG = 96;
const MINI_SAMPLES = 40;

/**
 * A probe's answer as a thumbnail: an analog port's waveform (thinned — it is analytic, so every
 * kept point is exact), or a stream's first samples (drawn as steps). `silent` when it reads 0.
 */
export function miniOf(probe: InternalProbe): MiniSignal {
  const volts = probe.waveform.volts;
  const flat = volts.every((v) => v === 0);
  if (probe.medium === "samples") return { kind: "samples", values: volts.slice(0, MINI_SAMPLES), silent: flat };
  const step = Math.max(1, Math.ceil(volts.length / MINI_ANALOG));
  return { kind: "analog", values: volts.filter((_, i) => i % step === 0), silent: flat };
}

/** A thumbnail's path: steps for samples, a line for an analog signal; a silent port has none. */
export function miniPath(mini: MiniSignal, width: number, height: number): string {
  if (mini.silent) return "";
  return (mini.kind === "samples" ? stairs(mini.values, width, height, 2) : trace(mini.values, width, height, 2)).d;
}

/** The probe can only say no in one way that is not a fault: validation mode keeps the signal back. */
export function withheldError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "status" in error && (error as { status: number }).status === 403;
}
