import { useEffect, useRef } from "react";
import type { Trace } from "@/api/client";
import { channelColor } from "@/lib/channels";

const DIV_H = 10;
const DIV_V = 8;

function cssVar(el: HTMLElement, name: string, fallback: string) {
  const v = getComputedStyle(el).getPropertyValue(name).trim();
  return v || fallback;
}

/**
 * Oscilloscope graticule and traces. Drawn on a canvas sized to the element and the device pixel
 * ratio; traces of 1000 points need no decimation, longer ones are reduced to min/max per pixel.
 */
export function WaveformCanvas({
  traces,
  triggerLevel,
  triggerChannel,
  className,
}: {
  traces: Trace[];
  triggerLevel?: number;
  triggerChannel?: number;
  className?: string;
}) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const draw = () => {
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

      // Traces.
      for (const tr of traces) {
        const color = resolveColor(canvas, channelColor(tr.channel));
        const n = tr.volts.length;
        if (!n) continue;
        const yOf = (v: number) => h / 2 - ((v - tr.offset) / tr.scale) * (h / DIV_V);
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.5;
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
          const per = n / w;
          for (let px = 0; px < w; px++) {
            let lo = Infinity;
            let hi = -Infinity;
            for (let i = Math.floor(px * per); i < Math.min(n, Math.floor((px + 1) * per)); i++) {
              lo = Math.min(lo, tr.volts[i]);
              hi = Math.max(hi, tr.volts[i]);
            }
            ctx.moveTo(px, clampY(yOf(hi), h));
            ctx.lineTo(px, clampY(yOf(lo), h) + 0.5);
          }
        }
        ctx.stroke();
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
    draw();
    const ro = new ResizeObserver(draw);
    ro.observe(canvas);
    return () => ro.disconnect();
  }, [traces, triggerLevel, triggerChannel]);

  return <canvas ref={ref} className={className} role="img" aria-label="waveform" />;
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

function resolveColor(el: HTMLElement, value: string): string {
  const m = value.match(/^var\((--[\w-]+)\)$/);
  return m ? cssVar(el, m[1], "#fc0") : value;
}
