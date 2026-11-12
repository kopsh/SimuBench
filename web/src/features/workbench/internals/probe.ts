import type { Schemas } from "@/api/client";
import type { T } from "@/i18n";
import { formatEng } from "@/lib/format";
import type { GraphEdge } from "./layout";

/**
 * The internal probe's pure parts (signal-chain.md 1.2, 1.3, #953): what a wire is labelled with,
 * which ports open a probe, and the pictures of a probed signal. Unit-tested in probe.test.ts.
 */

export type InternalProbe = Schemas["InternalProbe"];
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

/**
 * An analog port's lines as stems on a dB scale (the strongest at the top, `SPECTRUM_RANGE_DB`
 * below it at the bottom), frequency linear from 0 to a little past the highest line.
 */
export function lineStems(lines: readonly { freq_hz: number; amplitude: number }[], width: number, height: number, pad = 4): Plot & { top: number } {
  const shown = lines.filter((l) => l.amplitude > 0);
  if (!shown.length) return { d: "", lo: 0, hi: 0, top: 0 };
  const db = shown.map((l) => 20 * Math.log10(l.amplitude));
  const hi = Math.max(...db);
  const lo = hi - SPECTRUM_RANGE_DB;
  const top = Math.max(...shown.map((l) => l.freq_hz)) * 1.1 || 1;
  const y = (v: number) => height - pad - ((Math.max(v, lo) - lo) / SPECTRUM_RANGE_DB) * (height - 2 * pad);
  const d = shown
    .map((l, i) => `M${((l.freq_hz / top) * width).toFixed(1)},${(height - pad).toFixed(1)}V${y(db[i]).toFixed(1)}`)
    .join("");
  return { d, lo, hi, top };
}

/** How many dB of the spectrum the picture shows below its strongest point. */
export const SPECTRUM_RANGE_DB = 100;

/**
 * A frame's amplitude spectrum as bars from the bottom (a spectrum of a finite frame has a value at
 * every bin; a line between bins would suggest values that are not there). Linear frequency axis,
 * from 0 to half the rate; the top is the strongest bin, the bottom `SPECTRUM_RANGE_DB` below it.
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
