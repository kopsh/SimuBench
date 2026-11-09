import { ChevronsRight, Crosshair } from "lucide-react";
import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import type { InstrumentInfo } from "@/api/client";
import { scpiOutcome, useInstrumentReadings, useScpi } from "@/api/hooks";

import { Button } from "@/components/ui/button";
import { commit, DraftInput, type Parsed, SKIP } from "@/components/app/DraftInput";
import { cssVar, decimate, resolveColor } from "@/lib/canvas";
import { prefersReducedMotion, sweepDurationMs, sweepProgress } from "@/lib/motion";
import { channelColor } from "@/lib/channels";
import { engParts, formatEng, parseEngineering } from "@/lib/format";
import { StateField } from "../StateField";
import { ScreenIdentity } from "./parts";

const DIVS = 10;
const DB_PER_DIV = 10;

/**
 * Spectrum analyzer front panel (class template): trace on a 10 × 10 graticule with the reference
 * level at the top, the marker and its readout, the coupled settings with their auto state.
 * Window, RBW and sweep time are sent as SCPI so that the analyzer applies its couplings.
 */
export function SpectrumScreen({ sid, inst, readonly }: { sid: string; inst: InstrumentInfo; readonly: boolean }) {
  const { t } = useTranslation();
  const { values: v, query: readings, vars } = useInstrumentReadings(sid, inst);
  const scpi = useScpi(sid, inst.name);
  const n = (k: string) => Number(v[`sa.${k}`] ?? 0);
  const trace = readings.data?.traces.find((x) => x.name === "sa.trace1");

  const send = (command: string) =>
    scpi.mutate(command, {
      onSuccess: (r) => {
        const { ok, message } = scpiOutcome(r);
        if (!ok && message) toast.error(message);
      },
    });

  const marker = { x: n("marker_x"), y: n("marker_y") };
  const mx = engParts(marker.x, "Hz", 9);

  return (
    <div className="grid h-full auto-rows-min gap-3 overflow-auto p-3 xl:grid-cols-[1fr_300px]">
      <section className="bg-card rounded-lg border">
        <header className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b px-3 py-2 text-xs">
          <ScreenIdentity inst={inst} />
          <Flag on={!!v["sa.uncal"]} label="UNCAL" title={t("spectrum.uncalReason")} testId="uncal" />
          <Flag on={!!v["sa.overload"]} label={t("spectrum.overload")} testId="overload" />
          <span className="text-muted-foreground num ml-auto">
            {t("spectrum.reference")} {String(v["sa.reference"] ?? "INT")} · {t("spectrum.sweep")} #{n("sweep")}
          </span>
        </header>
        <div className="num text-muted-foreground flex flex-wrap gap-x-4 gap-y-1 border-b px-3 py-1.5 text-[11px]">
          <span>RBW {formatEng(n("rbw"), "Hz", 3)}</span>
          <span>VBW {formatEng(n("vbw"), "Hz", 3)}</span>
          <span>SWT {formatEng(n("sweep_time"), "s", 4)}</span>
          <span>ATT {n("attenuation").toFixed(0)} dB</span>
          <span>REF {n("ref_level").toFixed(1)} dBm</span>
          <span className="text-foreground ml-auto" data-testid="marker-readout">
            MKR {mx.mantissa} {mx.prefix}
            {mx.unit} · {marker.y.toFixed(3)} dBm
          </span>
        </div>
        <SpectrumCanvas
          values={trace?.values ?? []}
          refLevel={n("ref_level")}
          start={n("start")}
          stop={n("stop")}
          marker={marker}
          sweepTime={n("sweep_time")}
          live={!readonly}
          className="aspect-[16/9] w-full"
        />
        <footer className="num text-muted-foreground flex justify-between border-t px-3 py-1.5 text-[11px]">
          <span>
            {t("spectrum.start")} {formatEng(n("start"), "Hz", 7)}
          </span>
          <span>
            {t("spectrum.center")} {formatEng((n("start") + n("stop")) / 2, "Hz", 7)} · {t("spectrum.span")}{" "}
            {formatEng(n("stop") - n("start"), "Hz", 5)}
          </span>
          <span>
            {t("spectrum.stop")} {formatEng(n("stop"), "Hz", 7)}
          </span>
        </footer>
      </section>

      <aside className="space-y-3">
        <section className="bg-card space-y-2 rounded-lg border p-3">
          <h3 className="text-muted-foreground text-[11px] font-medium tracking-wide uppercase">{t("spectrum.marker")}</h3>
          <div className="flex gap-2">
            <Button size="sm" variant="outline" className="flex-1" disabled={readonly} onClick={() => send(":CALC:MARK:MAX")}>
              <Crosshair className="size-3.5" /> {t("spectrum.peak")}
            </Button>
            <Button size="sm" variant="outline" className="flex-1" disabled={readonly} onClick={() => send(":CALC:MARK:MAX:NEXT")}>
              <ChevronsRight className="size-3.5" /> {t("spectrum.nextPeak")}
            </Button>
          </div>
          {["marker.threshold", "marker.excursion"].map((k) => {
            const sv = vars.get(k);
            return sv ? <StateField key={k} sid={sid} instrument={inst.name} v={sv} disabled={readonly} /> : null;
          })}
        </section>
        <section className="bg-card space-y-1.5 rounded-lg border p-3">
          <h3 className="text-muted-foreground mb-1 text-[11px] font-medium tracking-wide uppercase">{t("spectrum.frequency")}</h3>
          <ScpiField label={t("spectrum.center")} value={(n("start") + n("stop")) / 2} unit="Hz" header=":SENS:FREQ:CENT" onSend={send} disabled={readonly} />
          <ScpiField label={t("spectrum.span")} value={n("stop") - n("start")} unit="Hz" header=":SENS:FREQ:SPAN" onSend={send} disabled={readonly} />
          <ScpiField label="RBW" value={n("rbw")} unit="Hz" header=":SENS:BAND:RES" onSend={send} disabled={readonly} />
          <ScpiField label={t("spectrum.sweepTime")} value={n("sweep_time")} unit="s" header=":SENS:SWE:TIME" onSend={send} disabled={readonly} />
          <div className="flex gap-2 pt-1">
            <Button size="sm" variant="ghost" className="h-7 flex-1 text-xs" disabled={readonly} onClick={() => send(":SENS:BAND:RES:AUTO ON;:SENS:BAND:VID:AUTO ON;:SENS:SWE:TIME:AUTO ON;:SENS:POW:RF:ATT:AUTO ON")}>
              {t("spectrum.allAuto")}
            </Button>
          </div>
        </section>
        <section className="bg-card space-y-1.5 rounded-lg border p-3">
          <h3 className="text-muted-foreground mb-1 text-[11px] font-medium tracking-wide uppercase">{t("spectrum.display")}</h3>
          {["display.ref_level", "detector", "trace.type", "aver.count", "sweep.points", "rosc.source", "init.continuous"].map((k) => {
            const sv = vars.get(k);
            return sv ? <StateField key={k} sid={sid} instrument={inst.name} v={sv} disabled={readonly} /> : null;
          })}
        </section>
      </aside>
    </div>
  );
}

/** A warning badge, shown only while it holds (an unlit UNCAL read as a warning, #279). */
function Flag({ on, label, title, testId }: { on: boolean; label: string; title?: string; testId?: string }) {
  if (!on) return null;
  return (
    <span className="bg-error/15 text-error rounded px-1.5 py-0.5 text-[11px] font-medium" title={title} data-testid={testId} role="status">
      {label}
    </span>
  );
}

/** A numeric setting written through SCPI, so the instrument applies its own rules. */
function ScpiField({
  label,
  value,
  unit,
  header,
  onSend,
  disabled,
}: {
  label: string;
  value: number;
  unit: string;
  header: string;
  onSend: (cmd: string) => void;
  disabled?: boolean;
}) {
  const shown = (() => {
    const p = engParts(value, unit, 7);
    return `${p.mantissa} ${p.prefix}`.trim();
  })();
  // Rule: engineering notation without a unit (the unit is fixed); unreadable or unchanged text is dropped.
  const parse = (typed: string): Parsed<number> => {
    const parsed = parseEngineering(typed);
    return parsed !== null && parsed !== value ? commit(parsed) : SKIP;
  };
  return (
    <div className="grid grid-cols-[minmax(5rem,1fr)_minmax(8rem,1.4fr)] items-center gap-x-3">
      <label className="text-muted-foreground truncate text-xs">{label}</label>
      <div className="relative flex items-center">
        <DraftInput
          text={shown}
          parse={parse}
          onCommit={(n) => onSend(`${header} ${n}`)}
          disabled={disabled}
          className="num h-7 pr-8 text-right text-xs"
          aria-label={label}
        />
        <span className="text-muted-foreground pointer-events-none absolute right-2 text-[11px]">{unit}</span>
      </div>
    </div>
  );
}

/** dBm against frequency: 10 divisions each way, the reference level on the top line. */
/**
 * The analyzer's graticule and trace. ``live``: a new trace repaints behind a sweep line moving
 * left to right, the previous one still showing ahead of it — as the instrument draws a sweep
 * (paced by its sweep time, within what the eye can follow). Off under prefers-reduced-motion.
 */
function SpectrumCanvas({
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
    const started = performance.now();
    const duration = sweepDurationMs(sweepTime);
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
      ctx.strokeStyle = cssVar(canvas, "--plot-grid", "#333");
      ctx.lineWidth = 1;
      ctx.setLineDash([1, 3]);
      for (let i = 1; i < DIVS; i++) {
        hline(ctx, 0, (i * h) / DIVS, w);
        vline(ctx, (i * w) / DIVS, 0, h);
      }
      ctx.setLineDash([]);
      ctx.strokeStyle = cssVar(canvas, "--plot-axis", "#555");
      ctx.strokeRect(0.5, 0.5, w - 1, h - 1);
      ctx.fillStyle = cssVar(canvas, "--plot-text", "#bbb");
      ctx.font = "10px var(--font-mono, monospace)";
      for (let i = 0; i <= DIVS; i += 2) ctx.fillText(`${(refLevel - i * DB_PER_DIV).toFixed(0)}`, 4, Math.max(10, (i * h) / DIVS - 3));

      const yOf = (db: number) => ((refLevel - db) / (DIVS * DB_PER_DIV)) * h;
      const clamp = (y: number) => Math.max(1, Math.min(h - 1, y));
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
        ctx.beginPath();
        if (n <= w * 2) {
          for (let i = 0; i < n; i++) {
            const x = (i / (n - 1)) * w;
            if (i) ctx.lineTo(x, clamp(yOf(vals[i])));
            else ctx.moveTo(x, clamp(yOf(vals[i])));
          }
        } else {
          const { lo, hi } = decimate(vals, w);
          for (let px = 0; px < w; px++) {
            ctx.moveTo(px, clamp(yOf(hi[px])));
            ctx.lineTo(px, clamp(yOf(lo[px])) + 0.5);
          }
        }
        ctx.stroke();
        ctx.restore();
      };
      const sweepX = progress * w;
      stroke(values, 0, sweepX, 1); // this sweep, up to the line
      if (progress < 1) {
        stroke(old, sweepX, w, 0.55); // the previous sweep, still ahead of the line
        ctx.strokeStyle = cssVar(canvas, "--plot-text", "#bbb");
        ctx.globalAlpha = 0.5;
        vline(ctx, sweepX, 0, h);
        ctx.globalAlpha = 1;
      }
      if (stop > start) {
        const x = ((marker.x - start) / (stop - start)) * w;
        const y = clamp(yOf(marker.y));
        ctx.fillStyle = cssVar(canvas, "--primary", "#4af");
        ctx.beginPath();
        ctx.moveTo(x, y - 9);
        ctx.lineTo(x + 5, y - 3);
        ctx.lineTo(x, y + 3);
        ctx.lineTo(x - 5, y - 3);
        ctx.closePath();
        ctx.fill();
      }
    };
    if (motion && old.length > 1) {
      const step = () => {
        const p = sweepProgress(performance.now() - started, duration);
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
  }, [values, refLevel, start, stop, marker.x, marker.y, sweepTime, live]);
  return <canvas ref={ref} className={className} role="img" aria-label="spectrum" />;
}

function hline(ctx: CanvasRenderingContext2D, x: number, y: number, w: number) {
  ctx.beginPath();
  ctx.moveTo(x, Math.round(y) + 0.5);
  ctx.lineTo(x + w, Math.round(y) + 0.5);
  ctx.stroke();
}

function vline(ctx: CanvasRenderingContext2D, x: number, y: number, h: number) {
  ctx.beginPath();
  ctx.moveTo(Math.round(x) + 0.5, y);
  ctx.lineTo(Math.round(x) + 0.5, y + h);
  ctx.stroke();
}
