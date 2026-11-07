import { useTranslation } from "react-i18next";
import type { InstrumentInfo } from "@/api/client";
import { useInstrumentReadings } from "@/api/hooks";
import { numeric } from "../model";
import { Chip, Readout, ScreenIdentity, SettingsGroups } from "./parts";

/**
 * RF signal generator front panel: carrier frequency and level, modulation and sweep state, the
 * timebase in use, and how many spectral lines the output carries (FM/PM to Carson's rule).
 */
export function RfGeneratorScreen({ sid, inst, readonly }: { sid: string; inst: InstrumentInfo; readonly: boolean }) {
  const { t } = useTranslation();
  const { values: v, num, vars } = useInstrumentReadings(sid, inst);
  const on = (k: string) => vars.get(k)?.value === true;
  const mode = String(vars.get("freq.mode")?.value ?? "CW");
  const external = vars.get("rosc.source")?.value === "EXTernal";
  const locked = v["reference.locked"] !== false;

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
          <ScreenIdentity inst={inst} />
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
                point: num("synth.point"),
                points: num("synth.points"),
              })}
            </span>
          )}
        </header>
        <div className="grid grid-cols-2 divide-x" aria-live="polite">
          <Readout label={t("rfgen.frequency")} value={num("synth.frequency")} unit="Hz" strong digits={10} testId="F" />
          <Readout label={t("rfgen.level")} value={numeric(vars.get("power.level")?.value)} unit="dBm" plain testId="L" />
        </div>
        <footer className="text-muted-foreground num flex flex-wrap gap-x-5 gap-y-1 border-t px-3 py-2 text-[11px]">
          <span>
            {t("rfgen.lines")} {num("mod.lines")}
          </span>
          {num("mod.beta") > 0 && (
            <>
              <span>β {num("mod.beta").toPrecision(4)}</span>
              <span>
                {t("rfgen.omitted")} {(num("mod.omitted_power") * 100).toFixed(3)} %
              </span>
            </>
          )}
        </footer>
      </section>
      <SettingsGroups groups={groups} vars={vars} sid={sid} inst={inst} readonly={readonly} compact />
    </div>
  );
}
