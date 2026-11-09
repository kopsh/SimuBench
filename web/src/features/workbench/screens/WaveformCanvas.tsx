import { useEffect, useRef } from "react";
import type { Trace } from "@/api/client";
import { cssVar, decimate, resolveColor } from "@/lib/canvas";
import { channelColor } from "@/lib/channels";
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
  traces,
  triggerLevel,
  triggerChannel,
  live = false,
  className,
}: {
  traces: Trace[];
  triggerLevel?: number;
  triggerChannel?: number;
  live?: boolean;
  className?: string;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const shown = useRef<Trace[] | undefined>(undefined);
  const history = useRef<Trace[][]>([]);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
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

      // Persistence: earlier acquisitions, oldest faintest, under the current one.
      ghosts.forEach((set, k) => {
        ctx.globalAlpha = PERSISTENCE_ALPHA[k] ?? 0;
        for (const tr of set) strokeTrace(ctx, tr, w, h, resolveColor(canvas, channelColor(tr.channel)), 1.25);
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
        strokeTrace(ctx, tr, w, h, color, 1.5);
        ctx.restore();
        // Channel ground marker on the left edge.
        const y0 = clampY(yOf(0), h);
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.moveTo(0, y0 - 5);
        ctx.lineTo(8, y0);
        ctx.lineTo(0, y0 + 5);
        ctx.fill();
      }

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
      ctx.beginPath();
      ctx.moveTo(w / 2 - 5, 0);
      ctx.lineTo(w / 2 + 5, 0);
      ctx.lineTo(w / 2, 7);
      ctx.fill();
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
  }, [traces, triggerLevel, triggerChannel, live]);

  return <canvas ref={ref} className={className} role="img" aria-label="waveform" />;
}

/** One channel's record as a polyline, or min/max per pixel when it is longer than 2 points a pixel. */
function strokeTrace(ctx: CanvasRenderingContext2D, tr: Trace, w: number, h: number, color: string, width: number) {
  const n = tr.volts.length;
  if (!n) return;
  const yOf = (v: number) => h / 2 - ((v - tr.offset) / tr.scale) * (h / DIV_V);
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.lineJoin = "round";
  ctx.beginPath();
  if (n <= w * 2) {
    for (let i = 0; i < n; i++) {
      const x = (i / (n - 1)) * w;
      const y = clampY(yOf(tr.volts[i]), h);
      if (i) ctx.lineTo(x, y);
      else ctx.moveTo(x, y);
    }
  } else {
    const { lo, hi } = decimate(tr.volts, w);
    for (let px = 0; px < w; px++) {
      ctx.moveTo(px, clampY(yOf(hi[px]), h));
      ctx.lineTo(px, clampY(yOf(lo[px]), h) + 0.5);
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

