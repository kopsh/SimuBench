import type { InstrumentInfo, StateVarInfo } from "@/api/client";
import { StateField } from "../StateField";
import { formatReadout, isOverload } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Pieces shared by instrument screens: large readouts, status chips, chart legends. */

export function Readout({
  label,
  value,
  unit,
  strong,
  plain,
  digits = 5,
  fixed,
  signed,
  overflow,
  testId,
}: {
  label: string;
  value: number;
  unit: string;
  strong?: boolean;
  plain?: boolean;
  digits?: number;
  /** Keep trailing zeros: an instrument display shows all its digits ("0.00000", not "0"). */
  fixed?: boolean;
  /** Show the sign of a positive value too ("+1.00000"), as a source-measure display does. */
  signed?: boolean;
  /** The display's overload word, shown when the value is an overload (9.9E+37 / 9.91E+37). */
  overflow?: string;
  testId?: string;
}) {
  const r = plain
    ? { mantissa: isOverload(value) ? "" : value.toFixed(3), prefix: "", unit, overload: isOverload(value) }
    : formatReadout(value, { unit, digits, fixed, signed });
  const p = r.overload ? { mantissa: overflow ?? "OVLD", prefix: "", unit: "" } : r;
  return (
    <div className="px-4 py-3">
      <div className="text-muted-foreground text-xs font-medium">{label}</div>
      <div className="mt-1 flex items-baseline gap-1">
        <span
          className={cn("num leading-none", strong ? "text-3xl font-semibold" : "text-2xl font-medium")}
          data-testid={`readout-${testId ?? label}`}
        >
          {p.mantissa}
        </span>
        <span className="text-muted-foreground text-sm">
          {p.prefix}
          {p.unit}
        </span>
      </div>
    </div>
  );
}

export function Chip({ on, tone, children }: { on: boolean; tone: "ok" | "warn" | "error"; children: React.ReactNode }) {
  return (
    <span
      className={cn(
        "rounded px-1.5 py-0.5 text-[11px] font-medium",
        !on
          ? "bg-muted text-muted-foreground/70"
          : tone === "ok"
            ? "bg-ok/15 text-ok"
            : tone === "warn"
              ? "bg-warn/20 text-warn"
              : "bg-error/15 text-error",
      )}
      aria-pressed={on}
    >
      {children}
    </span>
  );
}

export function Legend({ dashed, label }: { dashed: boolean; label: string }) {
  return (
    <span className="text-muted-foreground inline-flex items-center gap-1.5">
      <svg width="18" height="6" aria-hidden>
        <line x1="0" y1="3" x2="18" y2="3" stroke="var(--primary)" strokeWidth="2" strokeDasharray={dashed ? "4 3" : undefined} />
      </svg>
      {label}
    </span>
  );
}

/** A screen header's identity: the bench label (or name) and the model (issue 638 — seven
 * screens had the same two spans). A screen adds its indicators and controls beside these. */
export function ScreenIdentity({ inst }: { inst: InstrumentInfo }) {
  return (
    <>
      <span className="font-medium">{inst.label || inst.name}</span>
      <span className="text-muted-foreground num">{inst.model}</span>
    </>
  );
}

/** Settings groups: one bordered block per group, the bound state fields inside (issue 638 —
 * Smu/Lockin/RfGenerator had the same block; the others are single-group variants). */
export function SettingsGroups({
  groups,
  vars,
  sid,
  inst,
  readonly,
  compact = false,
}: {
  groups: [string, string[]][];
  vars: Map<string, StateVarInfo>;
  sid: string;
  inst: InstrumentInfo;
  readonly: boolean;
  compact?: boolean;
}) {
  return (
    <section data-testid="settings-groups" className="grid gap-3 md:grid-cols-2 2xl:grid-cols-4">
      {groups.map(([title, keys]) => (
        <div key={title} className="bg-card space-y-1.5 rounded-lg border p-3">
          <h3 className="text-muted-foreground mb-1 text-[11px] font-medium tracking-wide uppercase">{title}</h3>
          {keys.map((k) => {
            const sv = vars.get(k);
            return sv ? (
              <StateField key={k} sid={sid} instrument={inst.name} v={sv} disabled={readonly} compact={compact} />
            ) : null;
          })}
        </div>
      ))}
    </section>
  );
}
