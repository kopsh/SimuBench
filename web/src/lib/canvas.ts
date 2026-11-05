/** Canvas drawing helpers shared by the waveform and spectrum canvases (issue 639): one cssVar,
 * one var() color resolution, one min/max-per-pixel decimation — they had drifted into two
 * copies each across WaveformCanvas and SpectrumScreen. */

export function cssVar(el: HTMLElement, name: string, fallback: string): string {
  return getComputedStyle(el).getPropertyValue(name).trim() || fallback;
}

/** A color that may be a CSS ``var(--x)``: the concrete value to stroke or fill with. */
export function resolveColor(el: HTMLElement, color: string, fallback = "#fc0"): string {
  const match = color.match(/^var\((--[\w-]+)\)$/);
  return match ? cssVar(el, match[1], fallback) : color;
}

/** Min/max per pixel column for a trace longer than two samples per pixel: the pair per column
 * is the vertical line the canvas strokes, so a spike between samples survives the shrink. */
export function decimate(values: ArrayLike<number>, width: number): { lo: Float64Array; hi: Float64Array } {
  const n = values.length;
  const lo = new Float64Array(width).fill(Infinity);
  const hi = new Float64Array(width).fill(-Infinity);
  const per = n / width;
  for (let px = 0; px < width; px++) {
    const end = Math.min(n, Math.floor((px + 1) * per));
    for (let i = Math.floor(px * per); i < end; i++) {
      const v = values[i];
      if (v < lo[px]) lo[px] = v;
      if (v > hi[px]) hi[px] = v;
    }
  }
  return { lo, hi };
}
