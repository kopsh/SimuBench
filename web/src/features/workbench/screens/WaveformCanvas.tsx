import { useEffect, useRef, useState } from "react";
import type { Trace } from "@/api/client";
import { cssVar, decimate, resolveColor } from "@/lib/canvas";
import { channelColor } from "@/lib/channels";
import { formatEng } from "@/lib/format";
import { PERSISTENCE_ALPHA, prefersReducedMotion, pushHistory, sweepProgress } from "@/lib/motion";

/** How long a new acquisition takes to draw in, left to right, when the screen is live. */
const REVEAL_MS = 220;

const DIV_H = 10;
const DIV_V = 8;

/**
 * Oscilloscope graticule and traces. Drawn on a canvas sized to the element and the device pixel
 * ratio; traces of 1000 points need no decimation, longer ones are reduced to min/max per pixel.
 * ``live`` (the scope is acquiring) turns on the display's persistence: the last acquisitions
 * stay, fading, and each new one draws in from the left. Off under prefers-reduced-motion.
 */
export function WaveformCanvas({
  traces: tracesProp,
  triggerLevel,
  triggerChannel,
  live = false,
  markers = true,
  window: spanProp,
  axisChannel,
  label,
  className,
}: {
  traces: Trace[];
  triggerLevel?: number;
  triggerChannel?: number;
  live?: boolean;
  /** The ground and trigger markers (a spectrum has neither). */
  markers?: boolean;
  /** The time the screen spans (s from the trigger): a record is drawn at its own ``t0_s`` / ``dt_s``
   * inside it and the rest of the screen stays empty (#1333). Without it a trace fills the width. */
  window?: TimeWindow;
  /** The channel whose volts per division label the vertical axis, in its colour (#1335); with a
   * ``window`` the time axis is labelled too. Without it the graticule carries no numbers. */
  axisChannel?: number;
  /** An accessible name other than "waveform". */
  label?: string;
  className?: string;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  // A caller that builds a fresh array of the same traces each render must not make every render a
  // full redraw and a new persistence step (the build-mode lag): keep the previous array then.
  const [traces, setTraces] = useState(tracesProp);
  if (!sameTraces(traces, tracesProp)) setTraces(tracesProp);
  const spanLeft = spanProp?.left;
  const spanWidth = spanProp?.width;
  const shown = useRef<Trace[] | undefined>(undefined);
  const history = useRef<Trace[][]>([]);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const span = spanLeft === undefined || spanWidth === undefined ? undefined : { left: spanLeft, width: spanWidth };
    const motion = live && !prefersReducedMotion();
    // A new acquisition replaces the one on screen; that one joins the persistence history.
    if (shown.current !== traces) {
      history.current = motion ? pushHistory(history.current, shown.current?.length ? shown.current : undefined) : [];
      shown.current = traces;
    }
    const ghosts = history.current;
    const started = performance.now();
    let raf = 0;
    const draw = (progress = 1) => {
      const dpr = window.devicePixelRatio || 1;
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      if (!w || !h) return;
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      const ctx = canvas.getContext("2d")!;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.fillStyle = cssVar(canvas, "--plot-bg", "#15171c");
      ctx.fillRect(0, 0, w, h);

      // Graticule: 10 x 8 divisions with minor ticks on the center lines.
      const grid = cssVar(canvas, "--plot-grid", "#333");
      const axis = cssVar(canvas, "--plot-axis", "#555");
      ctx.lineWidth = 1;
      ctx.strokeStyle = grid;
      ctx.setLineDash([1, 3]);
      for (let i = 1; i < DIV_H; i++) line(ctx, (i * w) / DIV_H, 0, (i * w) / DIV_H, h);
      for (let j = 1; j < DIV_V; j++) line(ctx, 0, (j * h) / DIV_V, w, (j * h) / DIV_V);
      ctx.setLineDash([]);
      ctx.strokeStyle = axis;
      line(ctx, w / 2, 0, w / 2, h);
      line(ctx, 0, h / 2, w, h / 2);
      for (let i = 0; i <= DIV_H * 5; i++) line(ctx, (i * w) / (DIV_H * 5), h / 2 - 3, (i * w) / (DIV_H * 5), h / 2 + 3);
      for (let j = 0; j <= DIV_V * 5; j++) line(ctx, w / 2 - 3, (j * h) / (DIV_V * 5), w / 2 + 3, (j * h) / (DIV_V * 5));
      ctx.strokeRect(0.5, 0.5, w - 1, h - 1);

      drawAxes(ctx, canvas, w, h, traces.find((t) => t.channel === axisChannel), span);

      // Persistence: earlier acquisitions, oldest faintest, under the current one.
      ghosts.forEach((set, k) => {
        ctx.globalAlpha = PERSISTENCE_ALPHA[k] ?? 0;
        for (const tr of set) strokeTrace(ctx, tr, w, h, resolveColor(canvas, channelColor(tr.channel)), 1.25, span);
      });
      ctx.globalAlpha = 1;

      // Traces: the current acquisition, drawn in up to ``progress`` of the width.
      for (const tr of traces) {
        const color = resolveColor(canvas, channelColor(tr.channel));
        if (!tr.volts.length) continue;
        const yOf = (v: number) => h / 2 - ((v - tr.offset) / tr.scale) * (h / DIV_V);
        ctx.save();
        ctx.beginPath();
        ctx.rect(0, 0, Math.max(1, progress * w), h);
        ctx.clip();
        strokeTrace(ctx, tr, w, h, color, 1.5, span);
        ctx.restore();
        if (!markers) continue;
        // Channel ground marker on the left edge.
        const y0 = clampY(yOf(0), h);
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.moveTo(0, y0 - 5);
        ctx.lineTo(8, y0);
        ctx.lineTo(0, y0 + 5);
        ctx.fill();
      }

      if (!markers) return;
      // Trigger level marker on the right edge, trigger point at the top center.
      const trig = traces.find((t) => t.channel === triggerChannel);
      if (trig && triggerLevel !== undefined) {
        const y = clampY(h / 2 - ((triggerLevel - trig.offset) / trig.scale) * (h / DIV_V), h);
        ctx.fillStyle = resolveColor(canvas, channelColor(triggerChannel));
        ctx.beginPath();
        ctx.moveTo(w, y - 5);
        ctx.lineTo(w - 8, y);
        ctx.lineTo(w, y + 5);
        ctx.fill();
      }
      ctx.fillStyle = cssVar(canvas, "--plot-text", "#bbb");
      // The trigger point: at time 0, which the horizontal position moves off the centre.
      const tx = span ? (-span.left / span.width) * w : w / 2;
      if (tx >= 0 && tx <= w) {
        ctx.beginPath();
        ctx.moveTo(tx - 5, 0);
        ctx.lineTo(tx + 5, 0);
        ctx.lineTo(tx, 7);
        ctx.fill();
      }
    };
    if (motion && ghosts.length) {
      const step = () => {
        const p = sweepProgress(performance.now() - started, REVEAL_MS);
        draw(p);
        if (p < 1) raf = requestAnimationFrame(step);
      };
      step();
    } else draw();
    const ro = new ResizeObserver(() => draw());
    ro.observe(canvas);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [traces, triggerLevel, triggerChannel, live, markers, spanLeft, spanWidth, axisChannel]);

  return <canvas ref={ref} className={className} role="img" aria-label={label ?? "waveform"} />;
}

/** Whether two trace lists hold the very same trace objects, in the same order. */
export function sameTraces(a: readonly Trace[], b: readonly Trace[]) {
  return a === b || (a.length === b.length && a.every((t, i) => t === b[i]));
}

/** The label text on the graticule, kept separate from canvas drawing so offsets, horizontal
 * positions and trigger references have direct regression tests (#1341). */
export function axisLabels(tr: Pick<Trace, "scale" | "offset"> | undefined, span?: TimeWindow) {
  const volts = tr
    ? Array.from({ length: DIV_V }, (_, j) => formatEng(tr.offset + (DIV_V / 2 - j) * tr.scale, "V", 3))
    : [];
  const times = span
    ? Array.from({ length: DIV_H / 2 + 1 }, (_, k) => {
        const value = span.left + (2 * k * span.width) / DIV_H;
        const rounding = Number.EPSILON * DIV_H * Math.max(Math.abs(span.left), Math.abs(span.width));
        return formatEng(Math.abs(value) <= rounding ? 0 : value, "s", 3);
      })
    : [];
  return { volts, times };
}

/** The numbers on the graticule (#1335): the axis channel's volts at each division line down the left
 * edge, in its colour and following its scale and offset, and the time from the trigger under every
 * second division (left edge, trigger position and time per division show in them). */
function drawAxes(
  ctx: CanvasRenderingContext2D,
  canvas: HTMLCanvasElement,
  w: number,
  h: number,
  tr: Trace | undefined,
  span?: TimeWindow,
) {
  const labels = axisLabels(tr, span);
  ctx.font = `10px ${cssVar(canvas, "--font-mono", "monospace")}`;
  ctx.textBaseline = "alphabetic";
  if (tr) {
    ctx.fillStyle = resolveColor(canvas, channelColor(tr.channel));
    ctx.textAlign = "left";
    for (let j = 0; j < DIV_V; j++) {
      // the bottom line is left to the time axis's corner
      const y = (j * h) / DIV_V;
      ctx.fillText(labels.volts[j], 12, j === 0 ? y + 11 : y - 2);
    }
  }
  if (span) {
    ctx.fillStyle = cssVar(canvas, "--plot-text", "#bbb");
    for (let i = 0; i <= DIV_H; i += 2) {
      ctx.textAlign = i === 0 ? "left" : i === DIV_H ? "right" : "center";
      const x = (i * w) / DIV_H;
      ctx.fillText(labels.times[i / 2], i === 0 ? x + 4 : i === DIV_H ? x - 4 : x, h - 4);
    }
  }
}

/** Where a record's samples fall across ``w`` pixels, and the first and last sample inside the
 * screen: with a ``span`` by their own times, without one the record fills the width. */
export function traceSpan(tr: Pick<Trace, "t0_s" | "dt_s" | "volts">, w: number, span?: TimeWindow) {
  const n = tr.volts.length;
  const xOf = span
    ? (i: number) => ((tr.t0_s + i * tr.dt_s - span.left) / span.width) * w
    : (i: number) => (i / Math.max(n - 1, 1)) * w;
  let i0 = 0;
  let i1 = n - 1;
  if (span && tr.dt_s > 0) {
    i0 = Math.max(0, Math.floor((span.left - tr.t0_s) / tr.dt_s) - 1);
    i1 = Math.min(n - 1, Math.ceil((span.left + span.width - tr.t0_s) / tr.dt_s) + 1);
  }
  return { xOf, i0, i1 };
}

/** The stretch of time a screen shows: its left edge (s from the trigger) and its width (s). */
export type TimeWindow = { left: number; width: number };

/**
 * One channel's record as a polyline, or min/max per pixel when it is longer than 2 points a pixel.
 * With a ``span`` the record sits where its own times fall on the screen and only the part inside
 * the screen is drawn; without one it fills the width.
 */
function strokeTrace(
  ctx: CanvasRenderingContext2D,
  tr: Trace,
  w: number,
  h: number,
  color: string,
  width: number,
  span?: TimeWindow,
) {
  const n = tr.volts.length;
  if (!n) return;
  const yOf = (v: number) => h / 2 - ((v - tr.offset) / tr.scale) * (h / DIV_V);
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.lineJoin = "round";
  ctx.beginPath();
  const { xOf, i0, i1 } = traceSpan(tr, w, span);
  const count = i1 - i0 + 1;
  if (count < 1) return;
  const xa = xOf(i0);
  const xb = xOf(i1);
  if (count <= w * 2) {
    for (let i = i0; i <= i1; i++) {
      const y = clampY(yOf(tr.volts[i]), h);
      if (i > i0) ctx.lineTo(xOf(i), y);
      else ctx.moveTo(xOf(i), y);
    }
  } else {
    // more samples than pixels: min/max per pixel over the visible stretch
    const px0 = Math.max(0, Math.floor(xa));
    const px1 = Math.min(w - 1, Math.ceil(xb));
    const cols = Math.max(1, px1 - px0 + 1);
    const { lo, hi } = decimate(tr.volts.slice(i0, i1 + 1), cols);
    for (let k = 0; k < cols; k++) {
      ctx.moveTo(px0 + k, clampY(yOf(hi[k]), h));
      ctx.lineTo(px0 + k, clampY(yOf(lo[k]), h) + 0.5);
    }
  }
  ctx.stroke();
}

function line(ctx: CanvasRenderingContext2D, x0: number, y0: number, x1: number, y1: number) {
  ctx.beginPath();
  ctx.moveTo(Math.round(x0) + 0.5, Math.round(y0) + 0.5);
  ctx.lineTo(Math.round(x1) + 0.5, Math.round(y1) + 0.5);
  ctx.stroke();
}

function clampY(y: number, h: number) {
  return Math.max(1, Math.min(h - 1, y));
}

