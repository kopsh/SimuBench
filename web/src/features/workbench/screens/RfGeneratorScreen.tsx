import { useTranslation } from "react-i18next";
import type { InstrumentInfo } from "@/api/client";
import { useEvents } from "@/api/events";
import { useReadings } from "@/api/hooks";
import { numeric, stateMap } from "../model";
import { StateField } from "../StateField";
import { Chip, Readout } from "./parts";

/**
 * RF signal generator front panel: carrier frequency and level, modulation and sweep state, the
 * timebase in use, and how many spectral lines the output carries (FM/PM to Carson's rule).
 */
export function RfGeneratorScreen({ sid, inst, readonly }: { sid: string; inst: InstrumentInfo; readonly: boolean }) {
  const { t } = useTranslation();
  const version = useEvents((s) => s.version);
  const readings = useReadings(sid, inst.name, version);
  const v = readings.data?.values ?? {};
  const num = (k: string) => Number(v[k] ?? 0);
  const vars = stateMap(inst);
  const on = (k: string) => vars.get(k)?.value === true;
  const mode = String(vars.get("freq.mode")?.value ?? "CW");
  const external = vars.get("rosc.source")?.value === "EXTernal";
  const locked = v["core.reference_locked"] !== false;

  const groups: [string, string[]][] = [
    [t("rfgen.carrier"), ["freq.cw", "power.level", "output.enabled"]],
    [t("rfgen.modulation"), ["am.state", "am.depth", "am.rate", "fm.state", "fm.dev", "fm.rate", "pm.state", "pm.dev", "pm.rate"]],
    [t("rfgen.sweep"), ["freq.mode", "sweep.start", "sweep.stop", "sweep.points", "sweep.spacing", "sweep.dwell", "list.manual"]],
    [t("rfgen.reference"), ["rosc.source", "rosc.output"]],
  ];

  return (
    <div className="grid h-full auto-rows-min gap-3 overflow-auto p-3">
      <section className="bg-card rounded-lg border">
        <header className="flex flex-wrap items-center gap-2 border-b px-3 py-2 text-xs">
          <span className="font-medium">{inst.label || inst.name}</span>
          <span className="text-muted-foreground num">{inst.model}</span>
          <Chip on={on("output.enabled")} tone="ok">
            {t("rfgen.rfOn")}
          </Chip>
          <Chip on={on("am.state")} tone="ok">
            AM
          </Chip>
          <Chip on={on("fm.state")} tone="ok">
            FM
          </Chip>
          <Chip on={on("pm.state")} tone="ok">
            PM
          </Chip>
          <Chip on={external} tone={locked ? "ok" : "error"}>
            {external ? (locked ? t("rfgen.extLocked") : t("rfgen.extMissing")) : t("rfgen.internal")}
          </Chip>
          {mode !== "CW" && (
            <span className="text-muted-foreground num ml-auto">
              {t("rfgen.point", {
                point: num("core.point"),
                points: num("core.points"),
              })}
            </span>
          )}
        </header>
        <div className="grid grid-cols-2 divide-x" aria-live="polite">
          <Readout label={t("rfgen.frequency")} value={num("core.frequency")} unit="Hz" strong digits={10} testId="F" />
          <Readout label={t("rfgen.level")} value={numeric(vars.get("power.level")?.value)} unit="dBm" plain testId="L" />
        </div>
        <footer className="text-muted-foreground num flex flex-wrap gap-x-5 gap-y-1 border-t px-3 py-2 text-[11px]">
          <span>
            {t("rfgen.lines")} {num("core.lines")}
          </span>
          {num("core.beta") > 0 && (
            <>
              <span>β {num("core.beta").toPrecision(4)}</span>
              <span>
                {t("rfgen.omitted")} {(num("core.omitted_power") * 100).toFixed(3)} %
              </span>
            </>
          )}
        </footer>
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
