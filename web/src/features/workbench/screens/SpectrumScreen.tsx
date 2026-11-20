import { ChevronsRight, Crosshair, Repeat, StepForward } from "lucide-react";
import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import type { InstrumentInfo } from "@/api/client";
import { scpiOutcome, useClockMode, useInstrumentReadings, useScpi } from "@/api/hooks";
import { isCancelled } from "@/components/app/ClientsConfirm";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { isLive, liveRequest } from "../clockControl";

import { Button } from "@/components/ui/button";
import { commit, DraftInput, type Parsed, SKIP } from "@/components/app/DraftInput";
import { cssVar, resolveColor } from "@/lib/canvas";
import { prefersReducedMotion, sweepDurationMs } from "@/lib/motion";
import { channelColor } from "@/lib/channels";
import { engParts, formatEng, parseEngineering } from "@/lib/format";
import { clampY, mountPlot, plotSeries, type PlotLine, type PlotPaint } from "./PlotCanvas";
import { useCountOnScreen } from "../onScreen";
import { StateField } from "../StateField";
import { ScreenIdentity } from "./parts";

const DIVS = 10;
const DB_PER_DIV = 10;

/**
 * Spectrum analyzer front panel (class template): trace on a 10 × 10 graticule with the reference
 * level at the top, the marker and its readout, the coupled settings with their auto state.
 * Window, RBW and sweep time are sent as SCPI so that the analyzer applies its couplings.
 */
export function SpectrumScreen({
  sid,
  inst,
  readonly,
  clockMode = "wall",
}: {
  sid: string;
  inst: InstrumentInfo;
  readonly: boolean;
  clockMode?: string;
}) {
  const { t } = useTranslation();
  const clock = useClockMode(sid);
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

  const continuous = vars.get("init.continuous")?.value !== false;
  const status = sweepStatus(clockMode, continuous);
  // The analyzer's Cont and Single keys (#1666), in place of the switch: the setting is on screen.
  useCountOnScreen(inst.name, "init.continuous", vars.has("init.continuous"));
  // Cont from this screen means "let it sweep": on a paused clock nothing would move, so the clock
  // goes live too, and says so. A script's :INIT:CONT ON leaves the clock alone.
  const sweepContinuously = () => {
    send(":INIT:CONT ON");
    if (!isLive(clockMode))
      clock.mutate(liveRequest(1), {
        onSuccess: () => toast.info(t("spectrum.continuousLive")),
        onError: (e) => !isCancelled(e) && toast.error((e as Error).message),
      });
  };
  // Single, as on the instrument: continuous sweep off, then one sweep (or the averaging count).
  const sweepOnce = () => send(":INIT:CONT OFF;:INIT:IMM");
  const marker = { x: n("marker_x"), y: n("marker_y") };
  const mx = engParts(marker.x, "Hz", 9);

  return (
    <div className="grid h-full auto-rows-min gap-3 overflow-auto p-3 xl:grid-cols-[1fr_300px]">
      <section className="bg-card rounded-lg border">
        <header className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b px-3 py-2 text-xs">
          <ScreenIdentity inst={inst} />
          <Flag on={!!v["sa.uncal"]} label="UNCAL" title={t("spectrum.uncalReason")} testId="uncal" />
          <Flag on={!!v["sa.overload"]} label={t("spectrum.overload")} testId="overload" />
          {status === "single" && (
            <span className="bg-muted rounded px-1.5 py-0.5 text-[11px] font-medium" data-testid="hold" title={t("spectrum.hintSingle")}>
              HOLD
            </span>
          )}
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
        {vars.has("init.continuous") && (
          <section className="bg-card space-y-2 rounded-lg border p-3">
            <h3 className="text-muted-foreground text-[11px] font-medium tracking-wide uppercase">{t("spectrum.sweepControl")}</h3>
            <div className="flex gap-2" role="group" aria-label={t("spectrum.sweepControl")}>
              <SweepKey pressed={continuous} hint={t("spectrum.continuousSweepHint")} disabled={readonly} onClick={sweepContinuously} testId="sweep-continuous">
                <Repeat className="size-3.5" /> {t("spectrum.continuousSweep")}
              </SweepKey>
              <SweepKey pressed={!continuous} hint={t("spectrum.singleSweepHint")} disabled={readonly} onClick={sweepOnce} testId="sweep-single">
                <StepForward className="size-3.5" /> {t("spectrum.singleSweep")}
              </SweepKey>
            </div>
          </section>
        )}
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
          <ScpiField label={t("spectrum.center")} instrument={inst.name} path="freq.center" value={(n("start") + n("stop")) / 2} unit="Hz" header=":SENS:FREQ:CENT" onSend={send} disabled={readonly} />
          <ScpiField label={t("spectrum.span")} instrument={inst.name} path="freq.span" value={n("stop") - n("start")} unit="Hz" header=":SENS:FREQ:SPAN" onSend={send} disabled={readonly} />
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
          {["display.ref_level", "detector", "trace.type", "aver.count", "sweep.points", "rosc.source"].map((k) => {
            const sv = vars.get(k);
            return sv ? <StateField key={k} sid={sid} instrument={inst.name} v={sv} disabled={readonly} /> : null;
          })}
        </section>
      </aside>
    </div>
  );
}

/**
 * Why the trace may not move: ``single`` (continuous sweep off: HOLD), ``clock`` (command/stepped
 * clock: virtual time only passes on a command or Advance, D47), else ``running``. Single wins.
 */
export function sweepStatus(clockMode: string, continuous: boolean): "running" | "single" | "clock" {
  if (!continuous) return "single";
  return clockMode === "wall" || clockMode === "scaled" ? "running" : "clock";
}

/** One of the analyzer's sweep keys (Cont, Single): lit while it is the mode, the command in its tooltip. */
function SweepKey({
  pressed,
  hint,
  disabled,
  onClick,
  testId,
  children,
}: {
  pressed: boolean;
  hint: string;
  disabled: boolean;
  onClick: () => void;
  testId: string;
  children: React.ReactNode;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button size="sm" variant={pressed ? "default" : "outline"} className="flex-1" aria-pressed={pressed} disabled={disabled} onClick={onClick} data-testid={testId}>
          {children}
        </Button>
      </TooltipTrigger>
      <TooltipContent className="max-w-72">{hint}</TooltipContent>
    </Tooltip>
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
  instrument,
  path,
  label,
  value,
  unit,
  header,
  onSend,
  disabled,
}: {
  /**
   * The setting this field shows as it is set: counted as on the screen, so the inspector beside it
   * leaves it out (#1252). Not given for RBW and sweep time, whose fields read the value in use (the
   * automatic one, when auto): the inspector keeps their manual setting and its auto switch.
   */
  instrument?: string;
  path?: string;
  label: string;
  value: number;
  unit: string;
  header: string;
  onSend: (cmd: string) => void;
  disabled?: boolean;
}) {
  useCountOnScreen(instrument ?? "", path ?? "", !!(instrument && path));
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
    // The analyzer's own drawing over the shared frame (its dashed divisions and edges are behind
    // this): the dB numbers, this sweep and the one before it, the sweep line and the marker.
    const paint: PlotPaint = (ctx, w, h, progress) => {
      ctx.fillStyle = cssVar(canvas, "--plot-text", "#bbb");
      ctx.font = "10px var(--font-mono, monospace)";
      // Every other line is labelled; the top one, the reference level, carries the unit (#1222).
      for (let i = 0; i <= DIVS; i += 2)
        ctx.fillText(`${(refLevel - i * DB_PER_DIV).toFixed(0)}${i === 0 ? " dBm" : ""}`, 4, Math.max(10, (i * h) / DIVS - 3));

      const yOf = (db: number) => ((refLevel - db) / (DIVS * DB_PER_DIV)) * h;
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
      if (stop > start) {
        const x = ((marker.x - start) / (stop - start)) * w;
        const y = clampY(yOf(marker.y), h);
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
