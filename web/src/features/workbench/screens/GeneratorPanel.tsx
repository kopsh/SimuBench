import { useTranslation } from "react-i18next";
import type { InstrumentInfo } from "@/api/client";
import { channelColor } from "@/lib/channels";
import { displayUnit, engParts } from "@/lib/format";
import { cn } from "@/lib/utils";
import { numeric, stateMap } from "../model";
import { StateField } from "../StateField";

/** Function generator panel: one column per channel with a large frequency readout. */
export function GeneratorPanel({ sid, inst, readonly }: { sid: string; inst: InstrumentInfo; readonly: boolean }) {
  const { t } = useTranslation();
  const vars = stateMap(inst);
  const channels = Array.from({ length: inst.channels }, (_, i) => i + 1);
  return (
    <div className="grid h-full auto-rows-min gap-3 overflow-auto p-3 lg:grid-cols-2">
      {channels.map((n) => {
        const freq = vars.get(`ch${n}.frequency`);
        const amp = vars.get(`ch${n}.amplitude`);
        const out = vars.get(`ch${n}.output`);
        const f = engParts(numeric(freq?.value), displayUnit(freq?.unit), 7);
        const a = engParts(numeric(amp?.value), "Vpp", 4);
        const keys = ["function", "frequency", "amplitude", "offset", "phase", "load", "output"];
        return (
          <section key={n} className={cn("bg-card rounded-lg border", !!out?.value && "border-ok/60")}>
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
          </section>
        );
      })}
    </div>
  );
}
