import { BaseEdge, type Edge, EdgeLabelRenderer, type EdgeProps, getBezierPath } from "@xyflow/react";
import { memo } from "react";
import { useTranslation } from "react-i18next";
import type { NetLevel } from "@/api/client";
import { formatEng } from "@/lib/format";
import { cn } from "@/lib/utils";

export type WireEdgeData = {
  color: string;
  label?: string;
  highlighted?: boolean;
  issue?: "error" | "warning";
  /** A timebase or lock-in reference: drawn in the reference color, dash-dotted, tagged REF. */
  reference?: boolean;
  /** Why the signal on this wire's net is not exact (fidelity); shown as ≈ with the reasons. */
  notes?: string[];
  /** The net's live level (D44): grey at 0 V, flowing in the channel color while it carries a signal. */
  level?: NetLevel;
  /** The wire was drawn from its load to its source: flow the other way. */
  reverse?: boolean;
};
export type WireEdgeType = Edge<WireEdgeData, "wire">;

function WireEdgeImpl({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, selected }: EdgeProps<WireEdgeType>) {
  const [path, labelX, labelY] = getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition });
  const color =
    data?.issue === "error" ? "var(--status-error)" : data?.reference ? "var(--medium-reference)" : (data?.color ?? "var(--medium-electrical)");
  const approx = (data?.notes?.length ?? 0) > 0;
  const label = [data?.reference ? "REF" : null, data?.label, approx ? "≈" : null].filter(Boolean).join(" · ") || undefined;
  const emphasis = selected || data?.highlighted;
  const level = data?.level;
  const silent = !!level && !level.active && !data?.issue;
  const live = !!level?.active && !data?.issue && !data?.reference;
  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        className={cn(live && "wire-live")}
        data-level={level ? (level.active ? "live" : "silent") : undefined}
        style={{
          stroke: silent ? "var(--muted-foreground)" : color,
          strokeWidth: emphasis ? 3.5 : 2,
          opacity: emphasis ? 1 : silent ? 0.45 : 0.85,
          strokeDasharray: data?.issue ? "6 4" : data?.reference ? "10 3 2 3" : undefined,
          animationDirection: live && data?.reverse ? "reverse" : undefined,
        }}
      />
      {level && <LevelHover path={path} level={level} />}
      {label && (
        <EdgeLabelRenderer>
          <div
            className={cn(
              "bg-card num absolute rounded border px-1.5 py-0.5 text-[10px] font-medium shadow-sm",
              approx ? "pointer-events-auto cursor-help" : "pointer-events-none",
              emphasis && "border-primary",
            )}
            title={approx ? data?.notes?.join("\n") : undefined}
            data-testid={approx ? "wire-approx" : undefined}
            style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`, color }}
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

/** Hovering a wire shows its net's present level (from the virtual probe's numbers). */
function LevelHover({ path, level }: { path: string; level: NetLevel }) {
  const { t } = useTranslation();
  const text = level.active
    ? `${level.net} · DC ${formatEng(level.dc, "V", 3)} · RMS ${formatEng(level.rms, "V", 3)} · ${t("workbench.peak")} ${formatEng(level.peak, "V", 3)}`
    : `${level.net} · 0 V`;
  return (
    <path d={path} fill="none" stroke="transparent" strokeWidth={14} className="react-flow__edge-interaction" data-testid="wire-level">
      <title>{text}</title>
    </path>
  );
}

export const WireEdge = memo(WireEdgeImpl);
