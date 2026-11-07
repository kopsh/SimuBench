import { AlertCircle, Info, Minus, Plus } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { InstrumentInfo, Schemas, StateVarInfo } from "@/api/client";
import { useConfigure, useInstrumentReadings, usePanel } from "@/api/hooks";
import { remoteInstrument } from "@/components/app/ClientsConfirm";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { pickLabel } from "@/i18n";
import { displayUnit, formatReadout } from "@/lib/format";
import { cn } from "@/lib/utils";
import { applyOps } from "../edit/editor";
import { labelOf, numeric, optionLabel, stateMap } from "../model";
import { StateField } from "../StateField";
import { ScreenIdentity } from "./parts";

type Panel = Schemas["Panel"];
type PanelControl = Schemas["PanelControl"];
type PanelReadout = Schemas["PanelReadout"];
type PanelKey = Schemas["PanelKey"];
type Changes = [path: string, value: unknown][];

/**
 * A front panel drawn from the definition's `display.panel`, or from its class's default layout
 * (instrument-authoring.md 7.3): the main display with its readings, grouped controls, softkeys.
 *
 * In operate mode the controls set the running instrument (refused while a script holds it, #121).
 * In build mode (`initial` given) they show and set the bench file's initial settings, while the
 * main display reads the running session (#103).
 */
export function PanelScreen({
  sid,
  inst,
  initial,
  readonly,
  values,
}: {
  sid: string;
  inst: InstrumentInfo;
  initial?: InstrumentInfo;
  readonly: boolean;
  /** Readings to show instead of the session's (stories). */
  values?: Record<string, unknown>;
}) {
  const { t } = useTranslation();
  const panel = inst.display.panel as Panel;
  const softkeys = panel.softkeys ?? [];
  const { values: fetched, vars: live } = useInstrumentReadings(sid, inst, { enabled: !values });
  const readings = values ?? fetched ?? {};
  const configure = useConfigure(sid);
  const action = usePanel(sid, inst.name);
  const [error, setError] = useState<string | null>(null);

  // `initial` is the bench file's instrument, not the running one — its map stays here (D64's
  // hook carries the live one).
  const shown = initial ? stateMap(initial) : live;
  const building = !!initial;
  const disabled = !building && readonly;

  const write = async (changes: Changes) => {
    setError(null);
    if (building) {
      await applyOps(changes.map(([path, value]) => ({ op: "set_state", instrument: inst.name, path, value })));
      return;
    }
    for (const [path, value] of changes) {
      try {
        const r = await configure.mutateAsync({ instrument: inst.name, path, value });
        if (!r.ok) {
          setError(`${r.error?.code}, ${r.error?.message}`);
          return;
        }
      } catch (e) {
        if (!remoteInstrument(e)) setError((e as Error).message); // a remote instrument is reported by the hook
        return;
      }
    }
  };

  const visible = (panel.readouts ?? []).filter((r) => Object.entries(r.when ?? {}).every(([p, v]) => same(live.get(p)?.value, v)));
  const [primary, ...rest] = visible;

  return (
    <div className="grid h-full auto-rows-min gap-3 overflow-auto p-3 lg:grid-cols-[3fr_2fr]" data-testid="instrument-panel">
      <section className="bg-card rounded-lg border" aria-label={t("instPanel.display")}>
        <header className="flex flex-wrap items-center gap-2 border-b px-3 py-2 text-xs">
          <ScreenIdentity inst={inst} />
          {/* Where the layout comes from is for whoever builds the instrument, not part of its
              display: an info mark with the explanation on hover (#281). */}
          <Tooltip>
            <TooltipTrigger asChild>
              <Info
                className="text-muted-foreground ml-auto size-3.5"
                aria-label={inst.display.panel_source === "declared" ? t("instPanel.declared") : t("instPanel.classDefault")}
                data-testid="panel-source"
              />
            </TooltipTrigger>
            <TooltipContent>
              {inst.display.panel_source === "declared" ? t("instPanel.declared") : t("instPanel.classDefault")}
            </TooltipContent>
          </Tooltip>
        </header>
        <div aria-live="polite">
          {primary && <ReadoutView r={primary} value={readings[primary.reading]} big />}
          {rest.length > 0 && (
            <div className="grid grid-cols-2 divide-x border-t">
              {rest.map((r) => (
                <ReadoutView key={r.reading} r={r} value={readings[r.reading]} />
              ))}
            </div>
          )}
        </div>
        {softkeys.length > 0 && (
          <div role="group" aria-label={t("instPanel.softkeys")} className="flex flex-wrap gap-1.5 border-t px-3 py-2">
            {softkeys.map((k) => {
              const label = pickLabel(k.label, k.label_en);
              const lit = k.state != null && same(shown.get(k.state)?.value, k.value) && setsHold(shown, k.sets);
              return (
                <Button
                  key={label}
                  size="sm"
                  variant={lit ? "default" : "outline"}
                  aria-pressed={k.state != null ? lit : undefined}
                  disabled={disabled || (building && k.action != null)}
                  onClick={() => {
                    if (k.action) action.mutate(k.action, { onError: (e) => !remoteInstrument(e) && setError((e as Error).message) });
                    else if (k.state) void write([[k.state, k.value], ...Object.entries(k.sets ?? {})]);
                  }}
                >
                  {label}
                </Button>
              );
            })}
          </div>
        )}
        {error && (
          <p role="alert" className="text-error flex items-center gap-1.5 border-t px-3 py-1.5 text-xs">
            <AlertCircle className="size-3.5 shrink-0" />
            {t("workbench.refused")}: {error}
          </p>
        )}
      </section>
      <div className="space-y-3">
        {(panel.groups ?? []).map((g) => {
          const title = pickLabel(g.label, g.label_en);
          return (
            <section key={title} className="bg-card space-y-2 rounded-lg border p-3" aria-label={title}>
              <h3 className="text-muted-foreground text-[11px] font-medium tracking-wide uppercase">{title}</h3>
              {(g.controls ?? []).map((c) => {
                const v = shown.get(c.state);
                return v ? (
                  <ControlView key={c.state} sid={sid} inst={inst} c={c} v={v} vars={shown} building={building} disabled={disabled} write={write} />
                ) : null;
              })}
            </section>
          );
        })}
      </div>
    </div>
  );
}

function ReadoutView({ r, value, big }: { r: PanelReadout; value: unknown; big?: boolean }) {
  const { t } = useTranslation();
  const label = pickLabel(r.label, r.label_en, r.reading);
  let mantissa: string;
  let unit = "";
  if (typeof value === "number") {
    // A count (no unit, a whole number: points, totalize, a channel) is shown as one, not "0.00000".
    const p = formatReadout(value, {
      unit: r.unit ? displayUnit(r.unit) : "",
      digits: r.digits ?? 6,
      fixed: true,
      count: !r.unit && Number.isInteger(value),
    });
    mantissa = p.overload ? t("instPanel.overload") : p.mantissa;
    unit = p.overload ? "" : `${p.prefix}${p.unit}`;
  } else if (typeof value === "boolean") mantissa = value ? t("instPanel.on") : t("instPanel.off");
  else mantissa = value == null ? t("instPanel.noReading") : String(value);
  return (
    <div className={big ? "px-5 py-4" : "px-4 py-2.5"}>
      <div className="text-muted-foreground text-xs font-medium">{label}</div>
      <div className="mt-1 flex items-baseline gap-1.5">
        <span
          className={cn("num leading-none", big ? "text-5xl font-semibold tracking-tight" : "text-xl font-medium")}
          data-testid={`panel-readout-${r.reading}`}
        >
          {mantissa}
        </span>
        <span className={cn("text-muted-foreground", big ? "text-lg" : "text-sm")}>{unit}</span>
      </div>
    </div>
  );
}

function ControlView({
  sid,
  inst,
  c,
  v,
  vars,
  building,
  disabled,
  write,
}: {
  sid: string;
  inst: InstrumentInfo;
  c: PanelControl;
  v: StateVarInfo;
  vars: Map<string, StateVarInfo>;
  building: boolean;
  disabled: boolean;
  write: (changes: Changes) => Promise<void>;
}) {
  const { t } = useTranslation();
  const labelled: StateVarInfo = {
    ...v,
    label: c.label ?? v.label,
    label_en: c.label_en ?? v.label_en,
    display_unit: c.unit ?? v.display_unit,
  };
  const label = labelOf(labelled);

  if (c.control === "buttons") {
    const keys: PanelKey[] =
      c.keys ??
      (v.type === "bool"
        ? [
            { value: true, label: t("instPanel.on"), sets: {} },
            { value: false, label: t("instPanel.off"), sets: {} },
          ]
        : (v.options ?? []).map((o) => ({ value: o, label: optionLabel(v, o), sets: {} })));
    return (
      <div className="space-y-1">
        <div className="text-muted-foreground text-xs">{label}</div>
        <div role="group" aria-label={label} className="flex flex-wrap gap-1">
          {keys.map((k) => {
            const text = pickLabel(k.label, k.label_en, String(k.value));
            const lit = same(v.value, k.value) && setsHold(vars, k.sets);
            return (
              <Button
                key={text}
                size="sm"
                variant={lit ? "default" : "outline"}
                className="num h-7 min-w-14 px-2.5 text-xs"
                aria-pressed={lit}
                disabled={disabled || v.readonly}
                onClick={() => void write([[c.state, k.value], ...Object.entries(k.sets ?? {})])}
              >
                {text}
              </Button>
            );
          })}
        </div>
      </div>
    );
  }

  // number, switch and select are the settings list's own fields, with the panel's label and unit.
  const step = c.control === "number" && c.step != null ? c.step : null;
  const stepTo = (sign: 1 | -1) => {
    const next = numeric(v.value) + sign * (step ?? 0);
    const bounded = Math.min(v.max ?? Infinity, Math.max(v.min ?? -Infinity, next));
    void write([[c.state, Number(bounded.toPrecision(12))]]);
  };
  return (
    <StateField
      sid={sid}
      instrument={inst.name}
      v={labelled}
      disabled={disabled}
      onCommit={building ? (value) => void write([[c.state, value]]) : undefined}
      trailing={
        step != null ? (
          <span className="flex shrink-0">
            <Button size="icon" variant="outline" className="size-7" aria-label={`${label} ${t("instPanel.stepDown")}`} disabled={disabled} onClick={() => stepTo(-1)}>
              <Minus className="size-3.5" />
            </Button>
            <Button size="icon" variant="outline" className="-ml-px size-7" aria-label={`${label} ${t("instPanel.stepUp")}`} disabled={disabled} onClick={() => stepTo(1)}>
              <Plus className="size-3.5" />
            </Button>
          </span>
        ) : undefined
      }
    />
  );
}

/** A setting's value and a key's value are the same: numbers by value ("INF" included), others exactly. */
function same(a: unknown, b: unknown): boolean {
  if (typeof a === "boolean" || typeof b === "boolean") return a === b;
  if ((typeof a === "number" || typeof b === "number") && a != null && b != null) return numeric(a) === numeric(b);
  return a === b;
}

function setsHold(vars: Map<string, StateVarInfo>, sets: Record<string, unknown> | undefined): boolean {
  return Object.entries(sets ?? {}).every(([p, v]) => same(vars.get(p)?.value, v));
}
