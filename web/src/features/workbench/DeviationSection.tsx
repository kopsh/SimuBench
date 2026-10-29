import { useTranslation } from "react-i18next";
import type { Schemas } from "@/api/client";
import { formatEng } from "@/lib/format";
import { Section } from "./Inspector";

type DeviationInfo = Schemas["DeviationInfo"];

/** ±limit as the data sheet writes it: "±1 %", "±20", or "±15…30" for a table of limits. */
function limitText(d: DeviationInfo): string {
  const unit = d.unit ? ` ${d.unit}` : "";
  if (typeof d.limit === "number") return `±${formatEng(d.limit, "", 3).trim()}${unit}`;
  const values = Object.values((d.limit ?? {}) as Record<string, number>);
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  return `±${formatEng(lo, "", 3).trim()}…${formatEng(hi, "", 3).trim()}${unit}`;
}

function valueText(value: unknown): string {
  if (typeof value === "number") return formatEng(value, "", 6).trim();
  return JSON.stringify(value);
}

/**
 * Individual deviations (个体偏差): how far this unit's parameters sit from the model's nominal values.
 * The ranges are always shown; a value drawn from the bench seed only when the bench reveals true values.
 */
export function DeviationSection({ items, children }: { items: DeviationInfo[]; children?: React.ReactNode }) {
  const { t } = useTranslation();
  if (items.length === 0 && !children) return null;
  return (
    <Section title={t("deviation.title")} collapsible defaultOpen={false}>
      <div className="space-y-2">
        <p className="text-muted-foreground text-[11px]">{t("deviation.hint")}</p>
        {children}
        {items.length > 0 && (
          <dl className="grid grid-cols-[1fr_auto_auto] gap-x-3 gap-y-1 text-xs" aria-label={t("deviation.title")}>
            {items.map((d) => (
              <div key={d.param} className="contents">
                <dt className="font-mono break-all">{d.param}</dt>
                <dd className="text-muted-foreground num text-right">
                  {limitText(d)}
                  {d.distribution === "normal" ? ` · ${t("deviation.normal")}` : ""}
                </dd>
                <dd className="num text-right">
                  {!d.drawn ? (
                    <span className="text-muted-foreground">{t("deviation.nominal")}</span>
                  ) : d.value === null || d.value === undefined ? (
                    <span className="text-muted-foreground">{t("deviation.hidden")}</span>
                  ) : (
                    valueText(d.value)
                  )}
                </dd>
              </div>
            ))}
          </dl>
        )}
      </div>
    </Section>
  );
}
