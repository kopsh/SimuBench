import { BaseEdge, type Edge, EdgeLabelRenderer, type EdgeProps, getBezierPath } from "@xyflow/react";
import { memo } from "react";
import { cn } from "@/lib/utils";

export type WireEdgeData = { color: string; label?: string; highlighted?: boolean; issue?: "error" | "warning" };
export type WireEdgeType = Edge<WireEdgeData, "wire">;

function WireEdgeImpl({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, selected }: EdgeProps<WireEdgeType>) {
  const [path, labelX, labelY] = getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition });
  const color = data?.issue === "error" ? "var(--status-error)" : (data?.color ?? "var(--medium-electrical)");
  const emphasis = selected || data?.highlighted;
  return (
    <>
      <BaseEdge id={id} path={path} style={{
          stroke: color,
          strokeWidth: emphasis ? 3.5 : 2,
          opacity: emphasis ? 1 : 0.85,
          strokeDasharray: data?.issue ? "6 4" : undefined,
        }} />
      {data?.label && (
        <EdgeLabelRenderer>
          <div
            className={cn(
              "bg-card num pointer-events-none absolute rounded border px-1.5 py-0.5 text-[10px] font-medium shadow-sm",
              emphasis && "border-primary",
            )}
            style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`, color }}
          >
            {data.label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

export const WireEdge = memo(WireEdgeImpl);
