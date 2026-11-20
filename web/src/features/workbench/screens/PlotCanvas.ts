import { cssVar, decimate } from "@/lib/canvas";
import { sweepProgress } from "@/lib/motion";

/**
 * The frame both instrument plots draw (issue #1731): sizing the canvas to the element and the
 * device pixel ratio, the background, the dashed division grid and the solid frame around it,
 * the sweep-line animation and the resize observer — the part SpectrumScreen had copied out of
 * WaveformCanvas, which a third instrument screen would have copied again. A screen brings only
 * its own calibration: what it paints over the frame, and how its lines are rasterized.
 */

/** How one graticule line is rasterized. WaveformCanvas rounds both endpoints half a pixel into
 * the frame, the spectrum rounds only across the line; for the full-span division lines the
 * difference sits under the frame drawn over it afterwards, but the dash phase along the line
 * differs by half a pixel — so the frame takes the rasterizer as a parameter instead of picking
 * one screen's pixels for the other. */
export type PlotLine = (
  ctx: CanvasRenderingContext2D,
  x0: number,
  y0: number,
  x1: number,
  y1: number,
) => void;

/** What a screen paints inside the frame: its calibration, traces and markers, at the CSS-pixel
 * size ``w`` × ``h``; ``progress`` (0..1) is how far the sweep line has come, 1 when still. */
export type PlotPaint = (
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  progress: number,
) => void;

/** The frame of one instrument plot, set up and torn down by the screen's effect: draws once —
 * or, while ``animate`` and ``run`` hold, keeps repainting behind a sweep line moving left to
 * right, ``durationMs`` per sweep — and repaints on resize. Returns the cleanup that cancels
 * both. */
export function mountPlot(
  canvas: HTMLCanvasElement,
  paint: PlotPaint,
  {
    divH,
    divV,
    line,
    animate,
    run,
    durationMs,
  }: { divH: number; divV: number; line: PlotLine; animate: boolean; run: boolean; durationMs: number },
): () => void {
  let raf = 0;
  const started = performance.now();
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
    graticule(ctx, canvas, w, h, divH, divV, line);
    paint(ctx, w, h, progress);
  };
  if (animate && run) {
    const step = () => {
      const p = sweepProgress(performance.now() - started, durationMs);
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
}

/** The graticule both screens share: dashed division lines over ``divH`` × ``divV`` divisions and
 * the solid frame around them. A screen's own marks on top of it — the scope's center cross and
 * minor ticks, the spectrum's dB numbers — its ``paint`` draws. */
function graticule(
  ctx: CanvasRenderingContext2D,
  canvas: HTMLCanvasElement,
  w: number,
  h: number,
  divH: number,
  divV: number,
  line: PlotLine,
) {
  ctx.strokeStyle = cssVar(canvas, "--plot-grid", "#333");
  ctx.lineWidth = 1;
  ctx.setLineDash([1, 3]);
  for (let i = 1; i < divH; i++) line(ctx, (i * w) / divH, 0, (i * w) / divH, h);
  for (let j = 1; j < divV; j++) line(ctx, 0, (j * h) / divV, w, (j * h) / divV);
  ctx.setLineDash([]);
  ctx.strokeStyle = cssVar(canvas, "--plot-axis", "#555");
  ctx.strokeRect(0.5, 0.5, w - 1, h - 1);
}

/** Keep a y inside the plot: a pixel of margin top and bottom, so a stroke does not paint over
 * the frame and a pinned marker stays readable. */
export function clampY(y: number, h: number) {
  return Math.max(1, Math.min(h - 1, y));
}

/**
 * One series as a polyline, or min/max per pixel when the stretch on screen is longer than two
 * points a pixel (#639): the branch WaveformCanvas and the spectrum had each copied. ``i0``..
 * ``i1`` is the stretch of samples the plot shows (all of them by default), ``xOf`` maps a sample
 * index to an x and ``yOf`` a value to a y, kept a pixel inside the plot. Assumes stroke style
 * and width are set; begins and strokes the path itself.
 */
export function plotSeries(
  ctx: CanvasRenderingContext2D,
  values: readonly number[],
  {
    xOf,
    yOf,
    w,
    h,
    i0 = 0,
    i1 = values.length - 1,
  }: { xOf: (i: number) => number; yOf: (v: number) => number; w: number; h: number; i0?: number; i1?: number },
) {
  const count = i1 - i0 + 1;
  if (count < 1) return;
  const y = (v: number) => clampY(yOf(v), h);
  ctx.beginPath();
  if (count <= w * 2) {
    for (let i = i0; i <= i1; i++) {
      if (i > i0) ctx.lineTo(xOf(i), y(values[i]));
      else ctx.moveTo(xOf(i), y(values[i]));
    }
  } else {
    // more samples than pixels: min/max per pixel over the visible stretch
    const px0 = Math.max(0, Math.floor(xOf(i0)));
    const px1 = Math.min(w - 1, Math.ceil(xOf(i1)));
    const cols = Math.max(1, px1 - px0 + 1);
    const visible = i0 === 0 && i1 >= values.length - 1 ? values : values.slice(i0, i1 + 1);
    const { lo, hi } = decimate(visible, cols);
    for (let k = 0; k < cols; k++) {
      ctx.moveTo(px0 + k, y(hi[k]));
      ctx.lineTo(px0 + k, y(lo[k]) + 0.5);
    }
  }
  ctx.stroke();
}
