import { useTranslation } from "react-i18next";
import type { InstrumentInfo } from "@/api/client";
import { channelColor } from "@/lib/channels";
import { displayUnit, engParts, formatEng } from "@/lib/format";
import { cn } from "@/lib/utils";
import { numeric, stateMap } from "../model";
import { StateField } from "../StateField";

/** Function generator panel: one column per channel with a large frequency readout. */
export function GeneratorPanel({ sid, inst, readonly }: { sid: string; inst: InstrumentInfo; readonly: boolean }) {
  const { t } = useTranslation();
  const vars = stateMap(inst);
  const channels = Array.from({ length: inst.channels }, (_, i) => i + 1);
  return (
    <div className="grid h-full auto-rows-min gap-3 overflow-auto p-3 lg:auto-rows-fr lg:grid-cols-2">
      {channels.map((n) => {
        const freq = vars.get(`ch${n}.frequency`);
        const amp = vars.get(`ch${n}.amplitude`);
        const out = vars.get(`ch${n}.output`);
        const f = engParts(numeric(freq?.value), displayUnit(freq?.unit), 7);
        const a = engParts(numeric(amp?.value), "Vpp", 4);
        const keys = ["function", "frequency", "amplitude", "offset", "phase", "load", "output"];
        return (
          <section key={n} className={cn("bg-card flex flex-col rounded-lg border", !!out?.value && "border-ok/60")}>
            <header className="flex items-center gap-2 border-b px-3 py-2">
              <span className="num font-semibold" style={{ color: channelColor(n) }}>
                CH{n}
              </span>
              <span
                className={cn(
                  "num ml-auto rounded px-1.5 py-0.5 text-[11px] font-medium",
                  out?.value ? "bg-ok/15 text-ok" : "bg-muted text-muted-foreground",
                )}
              >
                {t("workbench.output")} {out?.value ? t("common.on") : t("common.off")}
              </span>
            </header>
            <div className="flex items-baseline gap-3 px-3 pt-3">
              <span className="num text-3xl leading-none font-medium">{f.mantissa}</span>
              <span className="text-muted-foreground text-sm">
                {f.prefix}
                {f.unit}
              </span>
              <span className="num text-muted-foreground ml-auto text-sm">
                {a.mantissa} {a.prefix}
                {a.unit}
              </span>
            </div>
            <div className="space-y-1.5 p-3">
              {keys.map((k) => {
                const v = vars.get(`ch${n}.${k}`);
                return v ? <StateField key={k} sid={sid} instrument={inst.name} v={v} disabled={readonly} /> : null;
              })}
            </div>
            <OutputPreview
              channel={n}
              on={!!out?.value}
              fn={String(vars.get(`ch${n}.function`)?.value ?? "SINE")}
              amplitude={numeric(amp?.value)}
              offset={numeric(vars.get(`ch${n}.offset`)?.value)}
              frequency={numeric(freq?.value)}
              duty={dutyOf(vars, n)}
              symmetry={numeric(vars.get(`ch${n}.symmetry`)?.value ?? vars.get(`ch${n}.ramp_symm`)?.value ?? 50)}
            />
          </section>
        );
      })}
    </div>
  );
}

function dutyOf(vars: ReturnType<typeof stateMap>, n: number): number {
  const v = vars.get(`ch${n}.duty`) ?? vars.get(`ch${n}.sq_duty`);
  return numeric(v?.value ?? 50);
}

/**
 * One period of a standard function at phase ``x`` (0…1), in −1…1. ``duty`` (%) shapes a square
 * or pulse, ``symmetry`` (%) a ramp (50 is a triangle). Unknown functions (ARB, NOISE, PRBS) and DC
 * give null: the preview then says what it cannot draw rather than invent a shape.
 */
export function outputShape(fn: string, x: number, duty = 50, symmetry = 50): number | null {
  const f = fn.toUpperCase();
  const p = x - Math.floor(x);
  if (f.startsWith("SIN")) return Math.sin(2 * Math.PI * p);
  if (f.startsWith("SQU") || f.startsWith("PULS")) return p < duty / 100 ? 1 : -1;
  if (f.startsWith("TRI")) return p < 0.5 ? -1 + 4 * p : 3 - 4 * p;
  if (f.startsWith("RAMP")) {
    const s = Math.min(1, Math.max(0, symmetry / 100));
    if (s >= 1) return -1 + 2 * p;
    if (s <= 0) return 1 - 2 * p;
    return p < s ? -1 + (2 * p) / s : 1 - (2 * (p - s)) / (1 - s);
  }
  if (f === "DC") return 0;
  return null;
}

/**
 * The channel's output as set, two periods (#1252): fills the screen below the settings, and shows
 * duty, symmetry and offset at a glance. Drawn from the settings, so it shows even with nothing
 * connected; what reaches a load is on the wire's probe.
 */
function OutputPreview({
  channel,
  on,
  fn,
  amplitude,
  offset,
  frequency,
  duty,
  symmetry,
}: {
  channel: number;
  on: boolean;
  fn: string;
  amplitude: number;
  offset: number;
  frequency: number;
  duty: number;
  symmetry: number;
}) {
  const { t } = useTranslation();
  const W = 400;
  const H = 160;
  const half = amplitude / 2;
  const span = Math.max(1e-9, Math.abs(offset) + half) * 1.25;
  const y = (v: number) => H / 2 - (v / span) * (H / 2);
  const dc = fn.toUpperCase() === "DC";
  const drawable = outputShape(fn, 0, duty, symmetry) !== null;
  const N = 400;
  const points = drawable
    ? Array.from({ length: N + 1 }, (_, i) => {
        const x = (i / N) * 2;
        const v = dc ? offset : offset + half * (outputShape(fn, x, duty, symmetry) ?? 0);
        return `${((i / N) * W).toFixed(1)},${y(v).toFixed(1)}`;
      }).join(" ")
    : "";
  const period = frequency > 0 ? formatEng(1 / frequency, "s", 4) : "—";
  return (
    <figure className="flex min-h-40 flex-1 flex-col border-t px-3 pt-2 pb-3" data-testid="generator-preview">
      <figcaption className="num text-muted-foreground flex justify-between pb-1 text-[11px]">
        <span>{formatEng(offset + half, "V", 4)}</span>
        <span>{on ? t("generator.previewPeriods", { period }) : t("generator.previewOff")}</span>
      </figcaption>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        className="bg-plot min-h-28 w-full flex-1 rounded"
        role="img"
        aria-label={t("generator.preview", { channel })}
      >
        <line x1={0} x2={W} y1={y(0)} y2={y(0)} stroke="var(--plot-grid)" strokeWidth={1} vectorEffect="non-scaling-stroke" />
        <line x1={W / 2} x2={W / 2} y1={0} y2={H} stroke="var(--plot-grid)" strokeWidth={1} strokeDasharray="3 3" vectorEffect="non-scaling-stroke" />
        {drawable && (
          <polyline
            points={points}
            fill="none"
            stroke={on ? channelColor(channel) : "var(--muted-foreground)"}
            strokeWidth={1.75}
            strokeOpacity={on ? 1 : 0.6}
            vectorEffect="non-scaling-stroke"
          />
        )}
        {!drawable && (
          <text x={W / 2} y={H / 2} textAnchor="middle" dominantBaseline="middle" fontSize={14} fill="var(--plot-text)">
            {fn}
          </text>
        )}
      </svg>
      <div className="num text-muted-foreground pt-1 text-[11px]">{formatEng(offset - half, "V", 4)}</div>
    </figure>
  );
}
