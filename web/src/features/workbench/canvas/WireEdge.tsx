import { BaseEdge, type Edge, EdgeLabelRenderer, type EdgeProps, getBezierPath, Position, useStore } from "@xyflow/react";
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

/** How far a wire runs straight out of its pin before it turns (past the pin name), and its clearance above a node. */
const STUB = 40;

const outward = (p: Position) => (p === Position.Left ? -1 : p === Position.Right ? 1 : 0);

interface Box {
  top: number;
  left: number;
  right: number;
}

/**
 * A wire whose pins face away from each other (an output on the right of a node that sits to the
 * right of its load) would bend back underneath both nodes as a curve, hiding which pins it joins
 * (#277). Such a wire leaves each pin straight, then runs above both nodes. Null when the pins
 * face each other and the usual curve reads well.
 */
export function aroundPath(
  s: { x: number; y: number; side: Position },
  t: { x: number; y: number; side: Position },
  boxes: Box[],
): [string, number, number] | null {
  const ds = outward(s.side);
  const dt = outward(t.side);
  if (!ds || !dt) return null;
  // The pins face each other when each points toward the other end, however close the nodes are.
  const facing = (t.x - s.x) * ds > 0 && (s.x - t.x) * dt > 0;
  if (facing) return null;
  const sx = s.x + ds * STUB;
  const tx = t.x + dt * STUB;
  // Above every node the run would cross, not only the two it joins.
  const lo = Math.min(sx, tx);
  const hi = Math.max(sx, tx);
  const crossed = boxes.filter((b) => b.right >= lo && b.left <= hi);
  const top = Math.min(s.y, t.y, ...crossed.map((b) => b.top)) - STUB;
  // Straight segments with rounded joins (stroke-linejoin) read as a drawn schematic wire.
  const path = [`M ${s.x} ${s.y}`, `L ${sx} ${s.y}`, `L ${sx} ${top}`, `L ${tx} ${top}`, `L ${tx} ${t.y}`, `L ${t.x} ${t.y}`].join(" ");
  return [path, (sx + tx) / 2, top];
}

/** The nodes' outlines, as one string so a wire re-renders only when a node moves or resizes. */
function boxesKey(state: { nodeLookup: Map<string, { internals: { positionAbsolute: { x: number; y: number } }; measured: { width?: number } }> }): string {
  const out: number[] = [];
  for (const n of state.nodeLookup.values()) {
    const { x, y } = n.internals.positionAbsolute;
    out.push(Math.round(y), Math.round(x), Math.round(x + (n.measured.width ?? 0)));
  }
  return out.join(",");
}

function parseBoxes(key: string): Box[] {
  if (!key) return [];
  const v = key.split(",").map(Number);
  const out: Box[] = [];
  for (let i = 0; i + 2 < v.length; i += 3) out.push({ top: v[i], left: v[i + 1], right: v[i + 2] });
  return out;
}

function WireEdgeImpl({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
  selected,
}: EdgeProps<WireEdgeType>) {
  const boxes = parseBoxes(useStore(boxesKey));
  const [path, labelX, labelY] =
    aroundPath({ x: sourceX, y: sourceY, side: sourcePosition }, { x: targetX, y: targetY, side: targetPosition }, boxes) ??
    getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition });
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
          strokeLinejoin: "round",
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
