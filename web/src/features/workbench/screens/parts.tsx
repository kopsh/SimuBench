import { engParts } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Pieces shared by instrument screens: large readouts, status chips, chart legends. */

export function Readout({
  label,
  value,
  unit,
  strong,
  plain,
  digits = 5,
  testId,
}: {
  label: string;
  value: number;
  unit: string;
  strong?: boolean;
  plain?: boolean;
  digits?: number;
  testId?: string;
}) {
  const p = plain ? { mantissa: value.toFixed(3), prefix: "", unit } : engParts(value, unit, digits);
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
