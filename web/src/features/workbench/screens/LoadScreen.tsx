import { useTranslation } from "react-i18next";
import type { InstrumentInfo } from "@/api/client";
import { useEvents } from "@/api/events";
import { useReadings } from "@/api/hooks";
import { stateMap } from "../model";
import { StateField } from "../StateField";
import { Chip, Readout } from "./parts";

/**
 * Electronic load front panel: input voltage, current and power, and whether the load holds its
 * setting (in constant current, below the knee its minimum resistance limits it instead).
 */
export function LoadScreen({ sid, inst, readonly }: { sid: string; inst: InstrumentInfo; readonly: boolean }) {
  const { t } = useTranslation();
  const version = useEvents((s) => s.version);
  const readings = useReadings(sid, inst.name, version);
  const v = readings.data?.values ?? {};
  const num = (k: string) => Number(v[k] ?? 0);
  const vars = stateMap(inst);
  const enabled = vars.get("input.enabled")?.value === true;
  const constantCurrent = vars.get("function.mode")?.value !== "CR";
  const regulating = !constantCurrent || v["load.regulating"] === true;

  return (
    <div className="grid h-full auto-rows-min gap-3 overflow-auto p-3 lg:grid-cols-[2fr_1fr]">
      <section className="bg-card rounded-lg border">
        <header className="flex flex-wrap items-center gap-2 border-b px-3 py-2 text-xs">
          <span className="font-medium">{inst.label || inst.name}</span>
          <span className="text-muted-foreground num">{inst.model}</span>
          <Chip on={enabled} tone="ok">
            {enabled ? t("load.inputOn") : t("load.inputOff")}
          </Chip>
          <span className="num rounded border px-1.5 py-0.5 text-[11px] font-semibold">{constantCurrent ? "CC" : "CR"}</span>
          <Chip on={enabled && !regulating} tone="warn">
            {t("load.belowKnee")}
          </Chip>
        </header>
        <div className="grid grid-cols-3 divide-x" aria-live="polite">
          <Readout label={t("smu.voltage")} value={num("load.voltage")} unit="V" strong digits={6} testId="V" />
          <Readout label={t("smu.current")} value={num("load.current")} unit="A" strong digits={6} testId="I" />
          <Readout label={t("smu.power")} value={num("load.power")} unit="W" digits={4} testId="P" />
        </div>
        <p className="text-muted-foreground border-t px-3 py-1.5 text-[11px]">{t("load.hint")}</p>
      </section>
      <section className="bg-card space-y-1.5 rounded-lg border p-3">
        <h3 className="text-muted-foreground mb-1 text-[11px] font-medium tracking-wide uppercase">{t("workbench.settings")}</h3>
        {["function.mode", "source.current.level", "source.resistance.level", "input.enabled"].map((k) => {
          const sv = vars.get(k);
          return sv ? <StateField key={k} sid={sid} instrument={inst.name} v={sv} disabled={readonly} /> : null;
        })}
      </section>
    </div>
  );
}
