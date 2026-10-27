import { BaseEdge, type Edge, EdgeLabelRenderer, type EdgeProps, getBezierPath } from "@xyflow/react";
import { memo } from "react";
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
};
export type WireEdgeType = Edge<WireEdgeData, "wire">;

function WireEdgeImpl({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, selected }: EdgeProps<WireEdgeType>) {
  const [path, labelX, labelY] = getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition });
  const color =
    data?.issue === "error" ? "var(--status-error)" : data?.reference ? "var(--medium-reference)" : (data?.color ?? "var(--medium-electrical)");
  const approx = (data?.notes?.length ?? 0) > 0;
  const label = [data?.reference ? "REF" : null, data?.label, approx ? "≈" : null].filter(Boolean).join(" · ") || undefined;
  const emphasis = selected || data?.highlighted;
  return (
    <>
      <BaseEdge id={id} path={path} style={{
          stroke: color,
          strokeWidth: emphasis ? 3.5 : 2,
          opacity: emphasis ? 1 : 0.85,
          strokeDasharray: data?.issue ? "6 4" : data?.reference ? "10 3 2 3" : undefined,
        }} />
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

export const WireEdge = memo(WireEdgeImpl);
