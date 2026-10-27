import { useTranslation } from "react-i18next";
import type { Schemas } from "@/api/client";
import { useEvents } from "@/api/events";
import { useNetProbe } from "@/api/hooks";
import { formatEng } from "@/lib/format";

type Probe = Schemas["NetProbe"];

/**
 * A virtual probe on a net (M4): the signal as it is, without loading the circuit or using an
 * instrument, and why it is what it is: every independent source's EMF times the circuit's
 * transfer function to this net, exactly as the engine computes it.
 */
export function ProbePanel({ sid, net }: { sid: string; net: string }) {
  const { t } = useTranslation();
  const version = useEvents((s) => s.version);
  const probe = useNetProbe(sid, net, version);
  const p = probe.data;
  if (!p) return <p className="text-muted-foreground text-xs">{t("common.loading")}</p>;
  return (
    <div className="space-y-3" data-testid="probe">
      <MiniWave p={p} />
      <dl className="num grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
        <dt className="text-muted-foreground">DC</dt>
        <dd className="text-right">{formatEng(p.dc, "V", 5)}</dd>
        <dt className="text-muted-foreground">RMS</dt>
        <dd className="text-right" data-testid="probe-rms">
          {formatEng(p.rms, "V", 5)}
        </dd>
        <dt className="text-muted-foreground">{t("probe.peak")}</dt>
        <dd className="text-right">{formatEng(p.peak, "V", 5)}</dd>
        <dt className="text-muted-foreground">{t("probe.lines")}</dt>
        <dd className="text-right">{p.line_count}</dd>
      </dl>
      {p.lines.length > 0 && (
        <table className="num w-full text-[11px]">
          <thead className="text-muted-foreground">
            <tr>
              <th className="py-0.5 text-left font-medium">{t("probe.frequency")}</th>
              <th className="py-0.5 text-right font-medium">{t("probe.amplitude")}</th>
            </tr>
          </thead>
          <tbody>
            {p.lines.slice(0, 6).map((l) => (
              <tr key={l.freq_hz} className="border-t">
                <td className="py-0.5">{formatEng(l.freq_hz, "Hz", 7)}</td>
                <td className="py-0.5 text-right">{formatEng(l.amplitude, "V", 5)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div>
        <h4 className="mb-1 text-xs font-medium">{t("probe.why")}</h4>
        <p className="text-muted-foreground mb-2 text-[11px]">{t("probe.whyHint")}</p>
        {p.sources.length === 0 && <p className="text-muted-foreground text-xs">{t("probe.noSource")}</p>}
        <ul className="space-y-2" data-testid="probe-sources">
          {p.sources.map((src) => (
            <li key={src.source} className="rounded-md border p-2">
              <div className="mb-1 flex items-center justify-between text-xs">
                <span className="num font-medium">{src.instrument ? `${src.instrument} · ${src.component}` : src.component}</span>
                <span className="text-muted-foreground num">RMS {formatEng(src.rms_contribution, "V", 4)}</span>
              </div>
              <table className="num w-full text-[11px]">
                <tbody>
                  {src.dc_contribution !== 0 && (
                    <tr>
                      <td className="text-muted-foreground py-0.5">DC</td>
                      <td className="py-0.5 text-right">
                        {formatEng(src.dc_emf, "V", 4)} × {src.dc_gain.toPrecision(4)} = {formatEng(src.dc_contribution, "V", 4)}
                      </td>
                    </tr>
                  )}
                  {src.lines.slice(0, 4).map((l) => (
                    <tr key={l.freq_hz}>
                      <td className="text-muted-foreground py-0.5">{formatEng(l.freq_hz, "Hz", 6)}</td>
                      <td className="py-0.5 text-right">
                        {formatEng(l.emf, "V", 4)} × {l.gain.toPrecision(4)}∠{l.phase_deg.toFixed(1)}° = {formatEng(l.contribution, "V", 4)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </li>
          ))}
        </ul>
      </div>
      {p.notes.length > 0 && (
        <ul className="text-warn space-y-0.5 text-[11px]">
          {p.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function MiniWave({ p }: { p: Probe }) {
  const { t } = useTranslation();
  const v = p.waveform.volts;
  const W = 280;
  const H = 72;
  const lo = Math.min(0, ...v);
  const hi = Math.max(0, ...v);
  const span = hi - lo || 1;
  const y = (x: number) => H - 4 - ((x - lo) / span) * (H - 8);
  const d = v.map((x, i) => `${i ? "L" : "M"}${((i / (v.length - 1)) * W).toFixed(1)},${y(x).toFixed(1)}`).join("");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="bg-background h-[72px] w-full rounded border" role="img" aria-label={t("probe.waveform")}>
      <line x1={0} x2={W} y1={y(0)} y2={y(0)} stroke="var(--border)" />
      <path d={d} fill="none" stroke="var(--primary)" strokeWidth="1.5" />
      <text x={4} y={11} className="num fill-muted-foreground text-[9px]">
        {formatEng(p.waveform.dt_s * v.length, "s", 3)}
      </text>
    </svg>
  );
}
