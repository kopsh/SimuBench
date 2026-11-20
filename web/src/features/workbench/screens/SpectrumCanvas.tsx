import { useEffect, useRef } from "react";
import { cssVar, resolveColor } from "@/lib/canvas";
import { channelColor } from "@/lib/channels";
import { prefersReducedMotion, sweepDurationMs } from "@/lib/motion";
import { clampY, mountPlot, plotSeries, type PlotLine, type PlotPaint } from "./PlotCanvas";

const DIVS = 10;
const DB_PER_DIV = 10;

/** The level graticule division line ``div`` stands for: the reference level on the top line (0)
 * and ``DB_PER_DIV`` dB less per line under it, down to the bottom of the screen. */
export function dbAt(div: number, refLevel: number) {
  return refLevel - div * DB_PER_DIV;
}

/** The numbers down the left edge, one under every other division: the reference level on the top
 * line carries the unit (#1222). */
export function dbLabels(refLevel: number): string[] {
  return Array.from({ length: DIVS / 2 + 1 }, (_, k) => `${dbAt(2 * k, refLevel).toFixed(0)}${k === 0 ? " dBm" : ""}`);
}

/** Where a level sits on the screen: the reference level on the top edge, ``DIVS * DB_PER_DIV`` dB
 * of height, so one division is ``h / DIVS``. */
export function dbY(db: number, refLevel: number, h: number) {
  return ((refLevel - db) / (DIVS * DB_PER_DIV)) * h;
}

/** Where the marker diamond lands: its frequency between ``start`` and ``stop`` across the width,
 * its level through the dB scale down the height, kept a pixel inside the plot. No span, no
 * diamond (``undefined``); a marker outside the span lands off the canvas, where it stays until
 * the next sweep moves it back. */
export function markerPoint(
  marker: { x: number; y: number },
  span: { start: number; stop: number },
  w: number,
  h: number,
  refLevel: number,
): { x: number; y: number } | undefined {
  if (!(span.stop > span.start)) return undefined;
  return {
    x: ((marker.x - span.start) / (span.stop - span.start)) * w,
    y: clampY(dbY(marker.y, refLevel, h), h),
  };
}

/** The spectrum's graticule rasterizer: rounded across the line, raw along it — the waveform's
 * rounds both endpoints, half a pixel of dash phase apart (see ``PlotLine``). */
const axisLine: PlotLine = (ctx, x0, y0, x1, y1) => {
  ctx.beginPath();
  if (y0 === y1) {
    ctx.moveTo(x0, Math.round(y0) + 0.5);
    ctx.lineTo(x1, Math.round(y0) + 0.5);
  } else {
    ctx.moveTo(Math.round(x0) + 0.5, y0);
    ctx.lineTo(Math.round(x0) + 0.5, y1);
  }
  ctx.stroke();
};

/**
 * The analyzer's graticule and trace: dBm against frequency, 10 divisions each way, the reference
 * level on the top line. ``live``: a new trace repaints behind a sweep line moving left to right,
 * the previous one still showing ahead of it — as the instrument draws a sweep (paced by its sweep
 * time, within what the eye can follow). Off under prefers-reduced-motion.
 */
export function SpectrumCanvas({
  values,
  refLevel,
  start,
  stop,
  marker,
  sweepTime = 0,
  live = false,
  className,
}: {
  values: number[];
  refLevel: number;
  start: number;
  stop: number;
  marker: { x: number; y: number };
  sweepTime?: number;
  live?: boolean;
  className?: string;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const shown = useRef<number[]>([]);
  const previous = useRef<number[]>([]);
  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const motion = live && !prefersReducedMotion();
    if (shown.current !== values) {
      previous.current = motion ? shown.current : [];
      shown.current = values;
    }
    const old = previous.current;
    // The analyzer's own drawing over the shared frame (its dashed divisions and edges are behind
    // this): the dB numbers, this sweep and the one before it, the sweep line and the marker.
    const paint: PlotPaint = (ctx, w, h, progress) => {
      ctx.fillStyle = cssVar(canvas, "--plot-text", "#bbb");
      ctx.font = "10px var(--font-mono, monospace)";
      const labels = dbLabels(refLevel);
      // Every other line is labelled; the top one, the reference level, carries the unit (#1222).
      for (let i = 0; i <= DIVS; i += 2) ctx.fillText(labels[i / 2], 4, Math.max(10, (i * h) / DIVS - 3));

      const yOf = (db: number) => dbY(db, refLevel, h);
      const color = resolveColor(canvas, channelColor(1));
      const stroke = (vals: number[], from: number, to: number, alpha: number) => {
        const n = vals.length;
        if (n < 2 || to <= from) return;
        ctx.save();
        ctx.beginPath();
        ctx.rect(from, 0, to - from, h);
        ctx.clip();
        ctx.globalAlpha = alpha;
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.25;
        plotSeries(ctx, vals, { xOf: (i) => (i / (n - 1)) * w, yOf, w, h });
        ctx.restore();
      };
      const sweepX = progress * w;
      stroke(values, 0, sweepX, 1); // this sweep, up to the line
      if (progress < 1) {
        stroke(old, sweepX, w, 0.55); // the previous sweep, still ahead of the line
        ctx.strokeStyle = cssVar(canvas, "--plot-text", "#bbb");
        ctx.globalAlpha = 0.5;
        axisLine(ctx, sweepX, 0, sweepX, h);
        ctx.globalAlpha = 1;
      }
      const at = markerPoint({ x: marker.x, y: marker.y }, { start, stop }, w, h, refLevel);
      if (at) {
        ctx.fillStyle = cssVar(canvas, "--primary", "#4af");
        ctx.beginPath();
        ctx.moveTo(at.x, at.y - 9);
        ctx.lineTo(at.x + 5, at.y - 3);
        ctx.lineTo(at.x, at.y + 3);
        ctx.lineTo(at.x - 5, at.y - 3);
        ctx.closePath();
        ctx.fill();
      }
    };
    return mountPlot(canvas, paint, {
      divH: DIVS,
      divV: DIVS,
      line: axisLine,
      animate: motion,
      run: old.length > 1,
      durationMs: sweepDurationMs(sweepTime),
    });
  }, [values, refLevel, start, stop, marker.x, marker.y, sweepTime, live]);
  return <canvas ref={ref} className={className} role="img" aria-label="spectrum" />;
}
