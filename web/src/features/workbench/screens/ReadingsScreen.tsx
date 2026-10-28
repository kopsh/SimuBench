import { useTranslation } from "react-i18next";
import type { InstrumentInfo } from "@/api/client";
import { useEvents } from "@/api/events";
import { useReadings } from "@/api/hooks";
import { engParts, formatEng } from "@/lib/format";
import { globalVars } from "../model";
import { StateField } from "../StateField";

const METER: Record<string, [string, string]> = { "meter.DC": ["meter.dc", "V"], "meter.AC": ["meter.ac", "V"] };

/**
 * Screens for instruments without a dedicated class template: a multimeter shows its readings as
 * large numbers; any other instrument shows its readings and settings (generic layout, D1).
 */
export function ReadingsScreen({ sid, inst, readonly }: { sid: string; inst: InstrumentInfo; readonly: boolean }) {
  const { t } = useTranslation();
  const version = useEvents((s) => s.version);
  const readings = useReadings(sid, inst.name, version);
  const entries = Object.entries(readings.data?.values ?? {});
  const settings = globalVars(inst);
  return (
    <div className="grid h-full auto-rows-min gap-3 overflow-auto p-3 md:grid-cols-2">
      <section className="bg-card rounded-lg border">
        <header className="border-b px-3 py-2 text-xs font-medium">
          {inst.label || inst.name} <span className="text-muted-foreground num ml-1">{inst.model}</span>
        </header>
        <div className="divide-y">
          {entries.length === 0 && <p className="text-muted-foreground p-3 text-xs">{t("meter.noReadings")}</p>}
          {entries.map(([key, value]) => {
            const meter = METER[key];
            if (meter && typeof value === "number") {
              const p = engParts(value, meter[1], 7);
              return (
                <div key={key} className="px-4 py-3">
                  <div className="text-muted-foreground text-xs font-medium">{t(meter[0])}</div>
                  <div className="mt-1 flex items-baseline gap-1">
                    <span className="num text-3xl leading-none font-semibold">{p.mantissa}</span>
                    <span className="text-muted-foreground text-sm">
                      {p.prefix}
                      {p.unit}
                    </span>
                  </div>
                </div>
              );
            }
            return (
              <div key={key} className="flex justify-between px-3 py-1.5 text-xs">
                <span className="text-muted-foreground num">{key}</span>
                <span className="num">{typeof value === "number" ? formatEng(value, "", 6) : String(value)}</span>
              </div>
            );
          })}
        </div>
      </section>
      {settings.length > 0 && (
        <section className="bg-card space-y-1.5 rounded-lg border p-3">
          <h3 className="text-muted-foreground mb-1 text-[11px] font-medium tracking-wide uppercase">{t("workbench.settings")}</h3>
          {settings.map((v) => (
            <StateField key={v.path} sid={sid} instrument={inst.name} v={v} disabled={readonly} compact />
          ))}
        </section>
      )}
    </div>
  );
}
