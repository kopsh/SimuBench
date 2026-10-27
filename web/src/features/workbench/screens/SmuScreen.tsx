import { useTranslation } from "react-i18next";
import type { InstrumentInfo } from "@/api/client";
import { useEvents } from "@/api/events";
import { useReadings } from "@/api/hooks";
import { formatEng } from "@/lib/format";
import { cn } from "@/lib/utils";
import { stateMap } from "../model";
import { StateField } from "../StateField";
import { Chip, Legend, Readout } from "./parts";

const OPEN = 9.9e37; // the SCPI "no value" a resistance reads with no current
const LIMIT_STAT = 8;

type Row = { v: number; i: number; r: number; t: number; stat: number };

/**
 * Source-measure unit front panel: the terminal's voltage and current, which loop holds it
 * (CV / CC / OFF) and whether the limit does, the measurement buffer as an I–V curve with the
 * limit drawn in, and the buffer table (D1: from the class template and the definition's state).
 */
export function SmuScreen({ sid, inst, readonly }: { sid: string; inst: InstrumentInfo; readonly: boolean }) {
  const { t } = useTranslation();
  const version = useEvents((s) => s.version);
  const readings = useReadings(sid, inst.name, version);
  const v = readings.data?.values ?? {};
  const num = (k: string) => Number(v[k] ?? 0);
  const regulation = String(v["core.regulation"] ?? "OFF");
  const limited = v["core.limited"] === true;
  const forcingVoltage = v["core.forcing"] !== "CURRent";
  const volts = num("core.voltage");
  const amps = num("core.current");
  const on = regulation !== "OFF";

  const trace = (name: string) => readings.data?.traces.find((x) => x.name === `core.buffer_${name}`)?.values ?? [];
  const [bv, bi, br, bt, bs] = ["v", "i", "r", "t", "stat"].map(trace);
  const rows: Row[] = bv.map((_, k) => ({
    v: bv[k],
    i: bi[k],
    r: br[k],
    t: bt[k],
    stat: bs[k],
  }));

  const vars = stateMap(inst);
  const groups: [string, string[]][] = [
    [t("smu.source"), ["source.function", "source.voltage.level", "source.current.level", "output.enabled"]],
    [t("smu.limits"), ["compliance.current.limit", "compliance.voltage.limit", "source.delay"]],
    [t("smu.measure"), ["sense.nplc", "sense.autozero", "system.line_freq"]],
    [t("smu.sweep"), ["sweep.mode", "sweep.function", "sweep.start", "sweep.stop", "sweep.points", "sweep.delay"]],
  ];

  return (
    <div className="grid h-full auto-rows-min gap-3 overflow-auto p-3">
      <section className="bg-card rounded-lg border">
        <header className="flex flex-wrap items-center gap-2 border-b px-3 py-2 text-xs">
          <span className="font-medium">{inst.label || inst.name}</span>
          <span className="text-muted-foreground num">{inst.model}</span>
          <Chip on={on} tone="ok">
            {on ? t("smu.outputOn") : t("smu.outputOff")}
          </Chip>
          <span className="num rounded border px-1.5 py-0.5 text-[11px] font-semibold" data-testid="smu-regulation">
            {regulation}
          </span>
          <Chip on={limited} tone="warn">
            {forcingVoltage ? t("smu.currentLimit") : t("smu.voltageLimit")}
          </Chip>
          <span className="text-muted-foreground num ml-auto">
            {t("smu.readingTime")} {formatEng(num("core.reading_time"), "s", 5)}
          </span>
        </header>
        <div className="grid grid-cols-2 divide-x divide-y md:grid-cols-4 md:divide-y-0" aria-live="polite">
          <Readout label={t("smu.voltage")} value={volts} unit="V" strong={forcingVoltage === limited} digits={6} testId="V" />
          <Readout label={t("smu.current")} value={amps} unit="A" strong={forcingVoltage !== limited} digits={6} testId="I" />
          <Readout label={t("smu.resistance")} value={amps !== 0 ? volts / amps : 0} unit="Ω" digits={5} testId="R" />
          <Readout label={t("smu.power")} value={volts * amps} unit="W" digits={4} testId="P" />
        </div>
        <footer className="text-muted-foreground num flex flex-wrap gap-x-5 gap-y-1 border-t px-3 py-2 text-[11px]">
          <span>
            {forcingVoltage ? t("smu.currentLimit") : t("smu.voltageLimit")} {formatEng(num("core.limit"), forcingVoltage ? "A" : "V", 4)}
          </span>
          <span>{t("smu.limitHint")}</span>
        </footer>
      </section>

      <section className="grid gap-3 lg:grid-cols-[3fr_2fr]">
        <div className="bg-card rounded-lg border">
          <header className="flex items-center gap-3 border-b px-3 py-2 text-xs">
            <span className="font-medium">{t("smu.ivCurve")}</span>
            <Legend dashed={false} label={t("smu.buffer")} />
            <Legend dashed label={t("smu.limit")} />
            <span className="text-muted-foreground num ml-auto">{t("smu.points", { count: rows.length })}</span>
          </header>
          <IvChart rows={rows} limit={num("core.limit")} forcingVoltage={forcingVoltage} />
        </div>
        <div className="bg-card flex min-h-0 flex-col rounded-lg border">
          <header className="border-b px-3 py-2 text-xs font-medium">{t("smu.bufferTable")}</header>
          <BufferTable rows={rows} />
        </div>
      </section>

      <section className="grid gap-3 md:grid-cols-2 2xl:grid-cols-4">
        {groups.map(([title, keys]) => (
          <div key={title} className="bg-card space-y-1.5 rounded-lg border p-3">
            <h3 className="text-muted-foreground mb-1 text-[11px] font-medium tracking-wide uppercase">{title}</h3>
            {keys.map((k) => {
              const sv = vars.get(k);
              return sv ? <StateField key={k} sid={sid} instrument={inst.name} v={sv} disabled={readonly} compact /> : null;
            })}
          </div>
        ))}
      </section>
    </div>
  );
}

/** The buffer as current against voltage; points read with the limit in control are marked. */
function IvChart({ rows, limit, forcingVoltage }: { rows: Row[]; limit: number; forcingVoltage: boolean }) {
  const { t } = useTranslation();
  const W = 520;
  const H = 250;
  const pad = { l: 60, r: 24, t: 12, b: 28 };
  if (rows.length === 0) {
    return <p className="text-muted-foreground flex h-64 items-center justify-center px-6 text-center text-xs">{t("smu.noBuffer")}</p>;
  }
  const vs = rows.map((r) => r.v);
  const is = rows.map((r) => r.i);
  // The limit bounds the axis it applies to: current when forcing voltage, voltage otherwise.
  const [vMin, vMax, vTicks] = niceAxis([...vs, ...(forcingVoltage ? [] : [limit, Math.min(...vs) < 0 ? -limit : 0])]);
  const [iMin, iMax, iTicks] = niceAxis([...is, ...(forcingVoltage ? [limit, Math.min(...is) < 0 ? -limit : 0] : [])]);
  const x = (vv: number) => pad.l + ((vv - vMin) / (vMax - vMin)) * (W - pad.l - pad.r);
  const y = (ii: number) => H - pad.b - ((ii - iMin) / (iMax - iMin)) * (H - pad.t - pad.b);
  const path = rows.map((r, k) => `${k ? "L" : "M"}${x(r.v).toFixed(1)},${y(r.i).toFixed(1)}`).join("");
  const limits = [limit, -limit].filter((l) => (forcingVoltage ? l >= iMin && l <= iMax : l >= vMin && l <= vMax));
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-64 w-full" role="img" aria-label={t("smu.ivCurve")}>
      {iTicks.map((ii) => (
        <g key={`i${ii}`}>
          <line x1={pad.l} x2={W - pad.r} y1={y(ii)} y2={y(ii)} stroke="var(--border)" strokeDasharray={ii === 0 ? undefined : "2 4"} />
          <text x={pad.l - 6} y={y(ii) + 3} textAnchor="end" className="num fill-muted-foreground text-[10px]">
            {formatEng(ii, "A", 3)}
          </text>
        </g>
      ))}
      {vTicks.map((vv) => (
        <g key={`v${vv}`}>
          <line x1={x(vv)} x2={x(vv)} y1={pad.t} y2={H - pad.b} stroke="var(--border)" strokeDasharray={vv === 0 ? undefined : "2 4"} />
          <text x={x(vv)} y={H - 10} textAnchor="middle" className="num fill-muted-foreground text-[10px]">
            {formatEng(vv, "V", 3)}
          </text>
        </g>
      ))}
      {limits.map((l) =>
        forcingVoltage ? (
          <line key={l} x1={pad.l} x2={W - pad.r} y1={y(l)} y2={y(l)} stroke="var(--status-warn)" strokeWidth="1.5" strokeDasharray="6 4" />
        ) : (
          <line key={l} x1={x(l)} x2={x(l)} y1={pad.t} y2={H - pad.b} stroke="var(--status-warn)" strokeWidth="1.5" strokeDasharray="6 4" />
        ),
      )}
      {rows.length > 1 && <path d={path} fill="none" stroke="var(--primary)" strokeWidth="2" />}
      {rows.map((r, k) => (
        <circle
          key={k}
          cx={x(r.v)}
          cy={y(r.i)}
          r={rows.length > 60 ? 1.5 : 3}
          fill={r.stat === LIMIT_STAT ? "var(--status-warn)" : "var(--primary)"}
        />
      ))}
    </svg>
  );
}

/** Axis bounds on a 1-2-5 grid around the values (and zero), with about five ticks. */
function niceAxis(values: number[]): [number, number, number[]] {
  const lo0 = Math.min(0, ...values);
  const hi0 = Math.max(0, ...values);
  const range = hi0 - lo0 || 1e-3;
  // A little headroom so the curve and the limit line keep off the frame; zero stays the edge.
  const lo = lo0 < 0 ? lo0 - 0.02 * range : 0;
  const hi = hi0 > 0 ? hi0 + 0.02 * range : 0;
  const raw = range / 4;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * mag).find((m) => m >= raw) ?? 10 * mag;
  const a = Math.floor(lo / step) * step;
  const b = Math.ceil(hi / step) * step;
  const ticks: number[] = [];
  for (let k = 0; a + k * step <= b + step / 2; k++) ticks.push(Math.abs(a + k * step) < step / 1e6 ? 0 : a + k * step);
  return [a, b, ticks];
}

function BufferTable({ rows }: { rows: Row[] }) {
  const { t } = useTranslation();
  if (rows.length === 0) return <p className="text-muted-foreground p-3 text-xs">{t("smu.noBuffer")}</p>;
  return (
    <div className="max-h-64 min-h-0 overflow-auto">
      <table className="num w-full text-[11px] whitespace-nowrap" data-testid="smu-buffer">
        <thead className="bg-card text-muted-foreground sticky top-0">
          <tr className="border-b">
            <th className="px-2 py-1 text-right font-medium">#</th>
            <th className="px-2 py-1 text-right font-medium">{t("smu.voltage")}</th>
            <th className="px-2 py-1 text-right font-medium">{t("smu.current")}</th>
            <th className="px-2 py-1 text-right font-medium">{t("smu.resistance")}</th>
            <th className="px-2 py-1 text-right font-medium">{t("smu.time")}</th>
            <th className="px-2 py-1 text-right font-medium">STAT</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, k) => (
            <tr key={k} className={cn("border-b last:border-0", r.stat === LIMIT_STAT && "bg-warn/10")}>
              <td className="text-muted-foreground px-2 py-0.5 text-right">{k}</td>
              <td className="px-2 py-0.5 text-right">{formatEng(r.v, "V", 6)}</td>
              <td className="px-2 py-0.5 text-right">{formatEng(r.i, "A", 6)}</td>
              <td className="px-2 py-0.5 text-right">{r.r >= OPEN ? "—" : formatEng(r.r, "Ω", 5)}</td>
              <td className="px-2 py-0.5 text-right">{formatEng(r.t, "s", 6)}</td>
              <td className="px-2 py-0.5 text-right">{r.stat}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
