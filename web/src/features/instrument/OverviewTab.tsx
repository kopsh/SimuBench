import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { InstrumentInfo } from "@/api/client";
import type { DefinitionData } from "@/api/hooks";
import { cn } from "@/lib/utils";
import { deviationLimit, panelView, specRows } from "./model";

function Card({ title, children, className }: { title: string; children: ReactNode; className?: string }) {
  return (
    <section className={cn("bg-card rounded-lg border", className)}>
      <header className="border-b px-3 py-2 text-xs font-medium">{title}</header>
      <div className="p-3">{children}</div>
    </section>
  );
}

function Rows({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[minmax(6rem,auto)_1fr] gap-x-3 gap-y-1.5 text-xs">
      {rows.map(([key, value]) => (
        <div key={key} className="contents">
          <dt className="text-muted-foreground">{key}</dt>
          <dd className="min-w-0 break-words">{value ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

/** The acceptance overview: identity and ports, the functions and ranges it offers, specs and
 * deviations side by side (instrument-authoring.md 7.2). */
export function OverviewTab({ definition, inst }: { definition: DefinitionData; inst: InstrumentInfo | undefined }) {
  const { t } = useTranslation();
  const panel = inst ? panelView(inst) : null;
  const specs = specRows(definition.specs);
  const deviations = inst?.deviations ?? [];
  const reference = definition.reference;
  return (
    <div className="grid auto-rows-min gap-3 p-4 xl:grid-cols-2">
      <Card title={t("acceptance.identity")}>
        <Rows
          rows={[
            [t("acceptance.manufacturer"), inst?.manufacturer ?? definition.manufacturer],
            [t("acceptance.model"), inst?.model ?? definition.model],
            [
              t("acceptance.classLabel"),
              t(`acceptance.class.${definition.class}`, { defaultValue: definition.class }),
            ],
            [t("acceptance.channels"), String(definition.channels?.count ?? 1)],
            [t("acceptance.version"), String(definition.version)],
            [t("acceptance.idn"), <span className="num">{inst?.idn ?? "—"}</span>],
          ]}
        />
        {definition.description && (
          <p className="text-muted-foreground mt-2 border-t pt-2 text-[11px]">{definition.description}</p>
        )}
        <div className="mt-3 border-t pt-3">
          <h4 className="text-muted-foreground mb-1.5 text-[11px] font-medium tracking-wide uppercase">
            {t("acceptance.ports")}
          </h4>
          {(definition.ports?.channel ?? {}) && Object.keys(definition.ports?.channel ?? {}).length > 0 && (
            <p className="text-muted-foreground mb-1 text-[11px]">
              {t("acceptance.channelPorts", { keys: Object.keys(definition.ports?.channel ?? {}).join("、") })}
            </p>
          )}
          <div className="flex flex-wrap gap-1.5">
            {Object.keys(definition.ports?.instrument ?? {}).length === 0 && (
              <span className="text-muted-foreground text-xs">{t("acceptance.noPorts")}</span>
            )}
            {Object.keys(definition.ports?.instrument ?? {}).map((name) => {
              const medium = definition.ports?.media?.[name] ?? "electrical";
              return (
                <span key={name} className="bg-muted inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px]">
                  <span className="num font-medium">{name}</span>
                  <span className="text-muted-foreground">{t(`internals.medium.${medium}`, { defaultValue: medium })}</span>
                </span>
              );
            })}
          </div>
        </div>
      </Card>

      <Card title={t("acceptance.reference")}>
        {reference ? (
          <Rows
            rows={[
              [t("acceptance.manual"), reference.title],
              [t("acceptance.document"), reference.document ?? "—"],
              [t("acceptance.referenceVersion"), reference.version ?? "—"],
              [t("acceptance.firmware"), reference.firmware ?? "—"],
            ]}
          />
        ) : (
          <p className="text-muted-foreground text-xs">{t("acceptance.customDefinition")}</p>
        )}
        {definition.template && (
          <p className="text-muted-foreground mt-3 border-t pt-3 text-[11px]">
            {t("acceptance.fromTemplate", { id: definition.template.id, version: definition.template.version })}
          </p>
        )}
      </Card>

      <Card title={t("acceptance.functions")} className="xl:col-span-2">
        {!panel || (panel.groups.length === 0 && panel.readouts.length === 0) ? (
          <p className="text-muted-foreground text-xs">{t("acceptance.noFunctions")}</p>
        ) : (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {panel.readouts.length > 0 && (
              <div>
                <h4 className="text-muted-foreground mb-1.5 text-[11px] font-medium tracking-wide uppercase">
                  {t("acceptance.readouts")}
                </h4>
                <div className="flex flex-wrap gap-1.5">
                  {panel.readouts.map((r) => (
                    <span key={r.reading} className="bg-muted rounded-md px-2 py-1 text-[11px]">
                      {r.label}
                      {r.unit ? <span className="text-muted-foreground ml-1">{r.unit}</span> : null}
                      <span className="text-muted-foreground num ml-1.5">{r.reading}</span>
                    </span>
                  ))}
                </div>
              </div>
            )}
            {panel.groups.map((g) => (
              <div key={g.label}>
                <h4 className="text-muted-foreground mb-1.5 text-[11px] font-medium tracking-wide uppercase">{g.label}</h4>
                <div className="space-y-2">
                  {g.controls.map((c) => (
                    <div key={`${c.state}.${c.label}`} className="text-[11px]">
                      <div className="flex items-baseline justify-between gap-2">
                        <span>{c.label}</span>
                        <span className="text-muted-foreground num shrink-0">{c.state}</span>
                      </div>
                      {c.choices && c.choices.length > 0 ? (
                        <div className="mt-0.5 flex flex-wrap gap-1">
                          {c.choices.map((choice) => (
                            <span key={choice} className="bg-muted rounded px-1.5 py-0.5 text-[10.5px]">
                              {choice}
                            </span>
                          ))}
                        </div>
                      ) : (
                        <div className="text-muted-foreground mt-0.5">
                          {c.kind === "switch" ? t("acceptance.onOff") : (c.range ?? "—")}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
        {inst && inst.display.panel_source && (
          <p className="text-muted-foreground mt-3 border-t pt-2 text-[11px]">
            {t(`acceptance.panelSource.${inst.display.panel_source}`)}
          </p>
        )}
      </Card>

      <Card title={t("acceptance.specs")}>
        {specs.length === 0 ? (
          <p className="text-muted-foreground text-xs">{t("acceptance.noSpecs")}</p>
        ) : (
          <Rows rows={specs.map((r) => [r.key, <span className="num">{r.value}</span>] as [string, ReactNode])} />
        )}
      </Card>

      <Card title={t("acceptance.deviations")}>
        {deviations.length === 0 ? (
          <p className="text-muted-foreground text-xs">{t("acceptance.noDeviations")}</p>
        ) : (
          <div className="space-y-1.5 text-xs">
            {deviations.map((d) => (
              <div key={d.param} className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                <span className="num">{d.param}</span>
                <span className="num">{deviationLimit(d.limit, d.unit, d.bands)}</span>
                <span className="text-muted-foreground">{d.distribution}</span>
                {d.value != null && (
                  <span className="text-muted-foreground">{t("acceptance.thisUnit", { value: String(d.value) })}</span>
                )}
              </div>
            ))}
            <p className="text-muted-foreground pt-1 text-[11px]">{t("acceptance.deviationsHint")}</p>
          </div>
        )}
      </Card>
    </div>
  );
}
