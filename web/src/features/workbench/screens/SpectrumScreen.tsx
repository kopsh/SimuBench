import { ChevronsRight, Crosshair } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import type { InstrumentInfo } from "@/api/client";
import { useEvents } from "@/api/events";
import { useReadings, useScpi } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { channelColor } from "@/lib/channels";
import { engParts, formatEng, parseEngineering } from "@/lib/format";
import { cn } from "@/lib/utils";
import { stateMap } from "../model";
import { StateField } from "../StateField";

const DIVS = 10;
const DB_PER_DIV = 10;

/**
 * Spectrum analyzer front panel (class template): trace on a 10 × 10 graticule with the reference
 * level at the top, the marker and its readout, the coupled settings with their auto state.
 * Window, RBW and sweep time are sent as SCPI so that the analyzer applies its couplings.
 */
export function SpectrumScreen({ sid, inst, readonly }: { sid: string; inst: InstrumentInfo; readonly: boolean }) {
  const { t } = useTranslation();
  const version = useEvents((s) => s.version);
  const readings = useReadings(sid, inst.name, version);
  const scpi = useScpi(sid, inst.name);
  const v = readings.data?.values ?? {};
  const n = (k: string) => Number(v[`sa.${k}`] ?? 0);
  const trace = readings.data?.traces.find((x) => x.name === "sa.trace1");
  const vars = stateMap(inst);

  const send = (command: string) =>
    scpi.mutate(command, {
      onSuccess: (r) => {
        if (r.errors.length) toast.error(`${r.errors[0].code}, ${r.errors[0].message}`);
      },
    });

  const marker = { x: n("marker_x"), y: n("marker_y") };
  const mx = engParts(marker.x, "Hz", 9);

  return (
    <div className="grid h-full auto-rows-min gap-3 overflow-auto p-3 xl:grid-cols-[1fr_300px]">
      <section className="bg-card rounded-lg border">
        <header className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b px-3 py-2 text-xs">
          <span className="font-medium">{inst.label || inst.name}</span>
          <span className="text-muted-foreground num">{inst.model}</span>
          <Flag on={!!v["sa.uncal"]} label="UNCAL" />
          <Flag on={!!v["sa.overload"]} label={t("spectrum.overload")} />
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

function Flag({ on, label }: { on: boolean; label: string }) {
  return (
    <span className={cn("rounded px-1.5 py-0.5 text-[11px] font-medium", on ? "bg-error/15 text-error" : "bg-muted text-muted-foreground/70")} aria-pressed={on}>
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
  const [draft, setDraft] = useState<string | null>(null);
  const submit = () => {
    if (draft === null) return;
    const parsed = parseEngineering(draft);
    setDraft(null);
    if (parsed !== null && parsed !== value) onSend(`${header} ${parsed}`);
  };
  return (
    <div className="grid grid-cols-[minmax(5rem,1fr)_minmax(8rem,1.4fr)] items-center gap-x-3">
      <label className="text-muted-foreground truncate text-xs">{label}</label>
      <div className="relative flex items-center">
        <Input
          value={draft ?? shown}
          disabled={disabled}
          onFocus={() => setDraft(shown)}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={submit}
          onKeyDown={(e) => {
            if (e.key === "Enter") (e.target as HTMLInputElement).blur();
            if (e.key === "Escape") {
              setDraft(null);
              (e.target as HTMLInputElement).blur();
            }
          }}
          className="num h-7 pr-8 text-right text-xs"
          aria-label={label}
        />
        <span className="text-muted-foreground pointer-events-none absolute right-2 text-[11px]">{unit}</span>
      </div>
    </div>
  );
}

function cssVar(el: HTMLElement, name: string, fallback: string) {
  return getComputedStyle(el).getPropertyValue(name).trim() || fallback;
}

/** dBm against frequency: 10 divisions each way, the reference level on the top line. */
function SpectrumCanvas({
  values,
  refLevel,
  start,
  stop,
  marker,
  className,
}: {
  values: number[];
  refLevel: number;
  start: number;
  stop: number;
  marker: { x: number; y: number };
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
      const n = values.length;
      if (n > 1) {
        const m = channelColor(1);
        const match = m.match(/^var\((--[\w-]+)\)$/);
        ctx.strokeStyle = match ? cssVar(canvas, match[1], "#fc0") : m;
        ctx.lineWidth = 1.25;
        ctx.beginPath();
        if (n <= w * 2) {
          for (let i = 0; i < n; i++) {
            const x = (i / (n - 1)) * w;
            if (i) ctx.lineTo(x, clamp(yOf(values[i])));
            else ctx.moveTo(x, clamp(yOf(values[i])));
          }
        } else {
          const per = n / w;
          for (let px = 0; px < w; px++) {
            let lo = Infinity;
            let hi = -Infinity;
            for (let i = Math.floor(px * per); i < Math.min(n, Math.floor((px + 1) * per)); i++) {
              lo = Math.min(lo, values[i]);
              hi = Math.max(hi, values[i]);
            }
            ctx.moveTo(px, clamp(yOf(hi)));
            ctx.lineTo(px, clamp(yOf(lo)) + 0.5);
          }
        }
        ctx.stroke();
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
    draw();
    const ro = new ResizeObserver(draw);
    ro.observe(canvas);
    return () => ro.disconnect();
  }, [values, refLevel, start, stop, marker.x, marker.y]);
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
