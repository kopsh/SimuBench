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

/** The strongest bins of a frame's spectrum, DC left out, for the table under the picture. */
export function strongestBins(spectrum: NonNullable<InternalProbe["spectrum"]>, count = 4): { freq: number; level: number }[] {
  const bins = spectrum.freq_hz.map((freq, i) => ({ freq, level: spectrum.level[i] })).slice(1);
  bins.sort((a, b) => b.level - a.level || a.freq - b.freq);
  return bins.slice(0, count);
}

/** The probe can only say no in one way that is not a fault: validation mode keeps the signal back. */
export function withheldError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "status" in error && (error as { status: number }).status === 403;
}
