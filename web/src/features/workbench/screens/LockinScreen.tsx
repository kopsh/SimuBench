import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { InstrumentInfo } from "@/api/client";
import { useEvents } from "@/api/events";
import { useReadings } from "@/api/hooks";
import { formatEng } from "@/lib/format";
import { stateMap } from "../model";
import { StateField } from "../StateField";
import { Chip, Legend, Readout } from "./parts";

type Point = { t: number; r: number };
const HISTORY = 400;

/**
 * Lock-in front panel: X, Y, R and θ as large readouts, overload and lock indicators, and a chart
 * of R that joins what was read with where the filter is heading (D1: drawn from the class
 * template; every value comes from the instrument definition and its readings).
 */
export function LockinScreen({ sid, inst, readonly }: { sid: string; inst: InstrumentInfo; readonly: boolean }) {
  const { t } = useTranslation();
  const version = useEvents((s) => s.version);
  const readings = useReadings(sid, inst.name, version);
  const v = readings.data?.values ?? {};
  const num = (k: string) => Number(v[k] ?? 0);
  const overload = num("demod.overload");
  const locked = v["osc.locked"] !== false;
  const nowS = (readings.data?.now_ns ?? 0) / 1e9;

  // R as read, by virtual time: appended when new readings arrive (derived state, no effect);
  // starts over when time runs backwards (another session).
  const [points, setPoints] = useState<Point[]>([]);
  const [seen, setSeen] = useState(readings.data);
  if (readings.data && readings.data !== seen) {
    setSeen(readings.data);
    const r = Number(readings.data.values["demod.R"] ?? 0);
    setPoints((h) => {
      const last = h.at(-1);
      const base = last && nowS < last.t ? [] : h;
      return last && nowS === last.t ? base : [...base, { t: nowS, r }].slice(-HISTORY);
    });
  }
  const projected = readings.data?.traces.find((x) => x.name === "demod.R_projected");

  const vars = stateMap(inst);
  const groups: [string, string[]][] = [
    [t("lockin.reference"), ["reference.source", "reference.frequency", "reference.phase", "reference.harmonic"]],
    [t("lockin.settings"), ["input.sensitivity", "input.coupling", "filter.time_constant", "filter.slope"]],
    ["SINE OUT", ["sine_out.amplitude", "sine_out.enabled"]],
  ];

  return (
    <div className="grid h-full auto-rows-min gap-3 overflow-auto p-3">
      <section className="bg-card rounded-lg border">
        <header className="flex flex-wrap items-center gap-2 border-b px-3 py-2 text-xs">
          <span className="font-medium">{inst.label || inst.name}</span>
          <span className="text-muted-foreground num">{inst.model}</span>
          <Chip on={locked} tone="ok">
            {locked ? t("lockin.locked") : t("lockin.unlocked")}
          </Chip>
          <Chip on={!!(overload & 1)} tone="error">
            {t("lockin.inputOverload")}
          </Chip>
          <Chip on={!!(overload & 2)} tone="error">
            {t("lockin.outputOverload")}
          </Chip>
          <span className="text-muted-foreground num ml-auto">
            {t("lockin.detect")} {formatEng(num("osc.detect_frequency"), "Hz", 7)}
          </span>
        </header>
        <div className="grid grid-cols-2 divide-x divide-y md:grid-cols-4 md:divide-y-0" aria-live="polite">
          <Readout label="X" value={num("demod.X")} unit="V" />
          <Readout label="Y" value={num("demod.Y")} unit="V" />
          <Readout label="R" value={num("demod.R")} unit="V" strong />
          <Readout label="θ" value={num("demod.THETA")} unit="°" plain />
        </div>
        <footer className="text-muted-foreground num flex flex-wrap gap-x-5 gap-y-1 border-t px-3 py-2 text-[11px]">
          <span>
            {t("lockin.enbw")} {formatEng(num("demod.enbw"), "Hz", 5)}
          </span>
          <span>
            {t("lockin.settling")} {formatEng(num("demod.settling"), "s", 4)}
          </span>
          <span>
            {t("lockin.noise")} {formatEng(num("demod.noise_sigma"), "V", 3)}
          </span>
        </footer>
      </section>

      <section className="bg-card rounded-lg border">
        <header className="flex items-center gap-3 border-b px-3 py-2 text-xs">
          <span className="font-medium">{t("lockin.chart")}</span>
          <Legend dashed={false} label={t("lockin.history")} />
          <Legend dashed label={t("lockin.projected")} />
        </header>
        <SettlingChart history={points} projected={projected?.values ?? []} dt={projected?.dt_s ?? 0} now={nowS} />
        <p className="text-muted-foreground border-t px-3 py-1.5 text-[11px]">{t("lockin.chartHint")}</p>
      </section>

      <section className="grid gap-3 md:grid-cols-3">
        {groups.map(([title, keys]) => (
          <div key={title} className="bg-card space-y-1.5 rounded-lg border p-3">
            <h3 className="text-muted-foreground mb-1 text-[11px] font-medium tracking-wide uppercase">{title}</h3>
            {keys.map((k) => {
              const sv = vars.get(k);
              return sv ? <StateField key={k} sid={sid} instrument={inst.name} v={sv} disabled={readonly} /> : null;
            })}
          </div>
        ))}
      </section>
    </div>
  );
}

/** R against virtual time: past reads on the left of "now", the projected settling on the right. */
function SettlingChart({ history, projected, dt, now }: { history: Point[]; projected: number[]; dt: number; now: number }) {
  const { t } = useTranslation();
  const W = 800;
  const H = 220;
  const pad = { l: 56, r: 12, t: 12, b: 24 };
  const span = projected.length ? (projected.length - 1) * dt : 1;
  const tMin = Math.max(history[0]?.t ?? now, now - 2 * span);
  const tMax = now + span;
  const past = history.filter((p) => p.t >= tMin);
  const values = [...past.map((p) => p.r), ...projected, 0];
  const vMax = Math.max(...values) * 1.08 || 1;
  const x = (tt: number) => pad.l + ((tt - tMin) / (tMax - tMin || 1)) * (W - pad.l - pad.r);
  const y = (vv: number) => H - pad.b - (vv / vMax) * (H - pad.t - pad.b);
  const pathOf = (pts: [number, number][]) => pts.map(([a, b], i) => `${i ? "L" : "M"}${x(a).toFixed(1)},${y(b).toFixed(1)}`).join("");
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * vMax);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-56 w-full" role="img" aria-label={t("lockin.chart")}>
      {ticks.map((v) => (
        <g key={v}>
          <line x1={pad.l} x2={W - pad.r} y1={y(v)} y2={y(v)} stroke="var(--border)" strokeDasharray="2 4" />
          <text x={pad.l - 6} y={y(v) + 3} textAnchor="end" className="num fill-muted-foreground text-[10px]">
            {formatEng(v, "V", 3)}
          </text>
        </g>
      ))}
      <line x1={x(now)} x2={x(now)} y1={pad.t} y2={H - pad.b} stroke="var(--muted-foreground)" strokeDasharray="3 3" />
      <text x={x(now)} y={H - 8} textAnchor="middle" className="fill-muted-foreground text-[10px]">
        {t("lockin.now")} {formatEng(now, "s", 5)}
      </text>
      {past.length > 1 && (
        <path d={pathOf(past.map((p) => [p.t, p.r]))} fill="none" stroke="var(--primary)" strokeWidth="2" />
      )}
      {projected.length > 1 && (
        <path
          d={pathOf(projected.map((r, i) => [now + i * dt, r]))}
          fill="none"
          stroke="var(--primary)"
          strokeWidth="2"
          strokeDasharray="5 4"
          opacity="0.8"
        />
      )}
    </svg>
  );
}
