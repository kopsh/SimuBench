import {
  Background,
  BackgroundVariant,
  type Connection,
  ConnectionMode,
  Controls,
  type Edge,
  MiniMap,
  type Node,
  type NodeChange,
  ReactFlow,
  ReactFlowProvider,
  applyNodeChanges,
  useReactFlow,
  useStore,
} from "@xyflow/react";
import "@xyflow/react/dist/base.css";
import { type DragEvent, useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { EditOp, NetLevel, SessionInfo } from "@/api/client";
import { useEvents } from "@/api/events";
import { channelColor } from "@/lib/channels";
import { useTheme } from "@/lib/theme";
import { accessoryLabel, channelOfRef, isReferenceRef } from "../model";
import { useWorkbench } from "../store";
import { PartNode, type PartNodeType } from "./PartNode";
import { InstrumentNode, type InstrumentNodeType, type Issue } from "./InstrumentNode";
import { freeSpot, type Rect } from "./placement";
import { WireEdge, type WireEdgeType } from "./WireEdge";

const nodeTypes = { instrument: InstrumentNode, part: PartNode };
const edgeTypes = { wire: WireEdge };

/** MIME type of library items dragged onto the canvas. */
export const LIBRARY_MIME = "application/x-simubench-item";
export type LibraryItem = { kind: "instrument" | "part"; id: string };

type AnyNode = InstrumentNodeType | PartNodeType;
type Point = { x: number; y: number };

// Where "add" from the library places an item when it is not dropped: the middle of the view.
let viewCenter: (() => Point) | null = null;
export function centerOfView(): Point | null {
  return viewCenter?.() ?? null;
}

// The nodes on the canvas as rectangles (flow coordinates), for placing a new one beside them (#1258).
let nodeRects: (() => Rect[]) | null = null;
// Brings a point of the flow to the middle of the view, at the current zoom.
let centerOn: ((p: Point) => void) | null = null;
// A node's box once the canvas has measured it, or null before.
let nodeBox: ((id: string) => Rect | null) | null = null;
// Whether a box (flow coordinates) lies wholly inside the canvas on screen.
let inView: ((box: Rect) => boolean) | null = null;

/** A spot for a new node near `preferred` that covers no node of the bench (#1258). */
export function placeNew(preferred: Point): Point {
  const rects = nodeRects?.() ?? [];
  // A new card is about as large as the largest card already there.
  const size = rects.length
    ? { width: Math.max(...rects.map((r) => r.width)), height: Math.max(...rects.map((r) => r.height)) }
    : { width: 240, height: 140 };
  return freeSpot(rects, size, preferred);
}

/**
 * Bring the node just added (`id`) into view when it is not in view (#1258): the view is centred on
 * it at the current zoom while part of it lies outside the canvas — only then, so a node placed in
 * sight never moves the view under the user's next action.
 */
export function revealPlaced(id: string): void {
  // Checked every 100 ms for 2 s: on a slow machine the node is measured late and the inspector
  // narrows the canvas later still. The view moves only while the node is not wholly in sight.
  const started = Date.now();
  const check = () => {
    const box = nodeBox?.(id);
    if (box && !inView?.(box)) centerOn?.({ x: box.x + box.width / 2, y: box.y + box.height / 2 });
    if (Date.now() - started < 2000) window.setTimeout(check, 100);
  };
  window.setTimeout(check, 100);
}

export interface CanvasProps {
  view: SessionInfo;
  /** Node positions from the bench file; falls back to the session's layout. */
  layout?: Record<string, Point>;
  /** Connect, delete and drop from the library. */
  editable?: boolean;
  /** Node drags are written to the bench file. */
  onOps?: (ops: EditOp[]) => void;
  onDropItem?: (item: LibraryItem, at: Point) => void;
  issues?: Map<string, Issue>;
  /** Instruments and DUTs with an active fault. */
  faulted?: Set<string>;
  /** Live level of each net (D44); without it, wires are drawn as before, without signal state. */
  levels?: Map<string, NetLevel>;
}

function split(ref: string): [string, string] {
  const i = ref.lastIndexOf(".");
  return [ref.slice(0, i), ref.slice(i + 1)];
}

function positionsOf(view: SessionInfo, layout: Record<string, Point> | undefined): Record<string, Point> {
  const source = layout ?? view.layout;
  const out: Record<string, Point> = {};
  const names = [...view.instruments.map((i) => i.name), ...view.parts.map((d) => d.name)];
  names.forEach((name, i) => {
    const p = source[name];
    out[name] = p ? { x: Number(p.x) || 0, y: Number(p.y) || 0 } : { x: i * 320, y: 60 };
  });
  return out;
}

/** Direction of the port a wire end refers to, to flow from source to load. */
function directionOf(view: SessionInfo, ref: string): string | undefined {
  const [owner, port] = split(ref);
  const ports = view.instruments.find((i) => i.name === owner)?.ports ?? view.parts.find((d) => d.name === owner)?.ports;
  return ports?.find((p) => p.name === port)?.direction;
}

function CanvasInner({ view, layout, editable = false, onOps, onDropItem, issues, faulted, levels }: CanvasProps) {
  const { t } = useTranslation();
  const { selection, select } = useWorkbench();
  const theme = useTheme((s) => s.resolved);
  const flow = useReactFlow();
  const positions = useMemo(() => positionsOf(view, layout), [view, layout]);
  const activity = useEvents((s) => s.activity);

  const selectedNet =
    selection?.kind === "wire"
      ? view.wires.find((w) => w.index === selection.id)?.net
      : selection?.kind === "net"
        ? selection.id
        : null;

  const built = useMemo<AnyNode[]>(
    () => [
      ...view.instruments.map<InstrumentNodeType>((inst) => ({
        id: inst.name,
        type: "instrument",
        position: positions[inst.name],
        data: {
          inst,
          editable,
          issue: issues?.get(inst.name),
          fault: faulted?.has(inst.name),
          levels: levels
            ? Object.fromEntries(inst.ports.flatMap((p) => (p.net && levels.get(p.net) ? [[p.name, levels.get(p.net)!]] : [])))
            : undefined,
          activity: levels ? activity[inst.name] : undefined,
        },
      })),
      ...view.parts.map<PartNodeType>((part) => ({
        id: part.name,
        type: "part",
        position: positions[part.name],
        data: { part, editable, issue: issues?.get(part.name), fault: faulted?.has(part.name) },
      })),
    ],
    [view, positions, editable, issues, faulted, levels, activity],
  );
  const selectedId = selection?.kind === "instrument" || selection?.kind === "part" ? selection.id : null;
  const [nodes, setNodes] = useState<AnyNode[]>(() => built.map((n) => ({ ...n, selected: n.id === selectedId })));
  // Derived-state updates during render (no effects): new data keeps what React Flow measured and
  // selected; a selection made elsewhere (resource tree) is applied only when it differs, so that
  // clicks on the canvas are never interrupted by a rebuild.
  const [prevBuilt, setPrevBuilt] = useState(built);
  if (prevBuilt !== built) {
    setPrevBuilt(built);
    setNodes((ns) => {
      const old = new Map(ns.map((n) => [n.id, n]));
      return built.map((n) => {
        const o = old.get(n.id);
        // A node being dragged keeps the pointer's position until it is dropped.
        return o ? ({ ...o, data: n.data, position: o.dragging ? o.position : n.position } as AnyNode) : n;
      });
    });
  }
  const [prevSelected, setPrevSelected] = useState(selectedId);
  if (prevSelected !== selectedId) {
    setPrevSelected(selectedId);
    setNodes((ns) =>
      ns.some((n) => !!n.selected !== (n.id === selectedId)) ? ns.map((n) => ({ ...n, selected: n.id === selectedId })) : ns,
    );
  }

  const edges = useMemo<WireEdgeType[]>(
    () => {
      // A net wired to several loads says what it carries once, on its first wire.
      const read = new Set<string>();
      return view.wires.map((w) => {
        const first = !!w.net && !read.has(w.net);
        if (w.net) read.add(w.net);
        const [src, sh] = split(w.from);
        const [dst, th] = split(w.to);
        const ch = channelOfRef(view, w.to) ?? channelOfRef(view, w.from);
        return {
          id: `wire-${w.index}`,
          type: "wire",
          source: src,
          sourceHandle: sh,
          target: dst,
          targetHandle: th,
          selected: selection?.kind === "wire" && selection.id === w.index,
          data: {
            color: channelColor(ch),
            label: w.accessories.map((a) => accessoryLabel(a.type, a.params)).join(" · ") || undefined,
            reference: isReferenceRef(view, w.from) || isReferenceRef(view, w.to),
            notes: view.nets.find((n) => n.id === w.net)?.notes ?? [],
            highlighted: !!selectedNet && w.net === selectedNet,
            issue: issues?.get(`wire:${w.index}`),
            level: w.net ? levels?.get(w.net) : undefined,
            readout: first,
            reverse: directionOf(view, w.from) === "in" && directionOf(view, w.to) !== "in",
          },
        };
      });
    },
    [view, selection, selectedNet, issues, levels],
  );

  const onNodesChange = useCallback(
    (changes: NodeChange<AnyNode>[]) => {
      setNodes((ns) => applyNodeChanges(changes, ns));
      const moved = changes.filter((c) => c.type === "position" && c.position && !c.dragging);
      if (moved.length && onOps) {
        onOps(
          moved.flatMap((c) =>
            c.type === "position" && c.position
              ? [{ op: "move" as const, name: c.id, x: Math.round(c.position.x), y: Math.round(c.position.y) }]
              : [],
          ),
        );
      }
    },
    [onOps],
  );

  const onConnect = useCallback(
    (c: Connection) => {
      if (!onOps || !c.sourceHandle || !c.targetHandle) return;
      onOps([{ op: "connect", from: `${c.source}.${c.sourceHandle}`, to: `${c.target}.${c.targetHandle}` }]);
    },
    [onOps],
  );

  // Deletion goes through the bench file: React Flow only reports what the user deleted.
  const onBeforeDelete = useCallback(
    async ({ nodes: ns, edges: es }: { nodes: Node[]; edges: Edge[] }) => {
      if (!onOps) return false;
      const gone = new Set(ns.map((n) => n.id));
      const wires = es
        .filter((e) => !gone.has(e.source) && !gone.has(e.target))
        .map((e) => Number(e.id.slice(5)))
        .sort((a, b) => b - a); // highest index first, so earlier indices stay valid
      const ops: EditOp[] = [
        ...wires.map((index) => ({ op: "disconnect" as const, index })),
        ...ns.map((n) => ({ op: "remove" as const, name: n.id })),
      ];
      if (ops.length) {
        onOps(ops);
        select(null);
      }
      return false;
    },
    [onOps, select],
  );

  useEffect(() => {
    viewCenter = () => {
      const el = document.querySelector(".react-flow");
      const r = el?.getBoundingClientRect();
      if (!r) return { x: 0, y: 0 };
      return flow.screenToFlowPosition({ x: r.left + r.width / 2, y: r.top + r.height / 2 });
    };
    nodeRects = () =>
      flow.getNodes().map((n) => ({
        x: n.position.x,
        y: n.position.y,
        width: n.measured?.width ?? n.width ?? 240,
        height: n.measured?.height ?? n.height ?? 140,
      }));
    centerOn = (p) => void flow.setCenter(p.x, p.y, { zoom: flow.getZoom(), duration: 200 });
    inView = (box) => {
      const pane = document.querySelector(".react-flow")?.getBoundingClientRect();
      if (!pane) return true;
      const tl = flow.flowToScreenPosition({ x: box.x, y: box.y });
      const br = flow.flowToScreenPosition({ x: box.x + box.width, y: box.y + box.height });
      return tl.x >= pane.left && tl.y >= pane.top && br.x <= pane.right && br.y <= pane.bottom;
    };
    nodeBox = (id) => {
      const n = flow.getNode(id);
      const width = n?.measured?.width;
      const height = n?.measured?.height;
      return n && width && height ? { x: n.position.x, y: n.position.y, width, height } : null;
    };
    return () => {
      viewCenter = null;
      nodeRects = null;
      centerOn = null;
      nodeBox = null;
      inView = null;
    };
  }, [flow]);

  // Opening a bench fits it at a zoom where the node text stays readable (not below 0.93, so
  // 13 px titles stay ≥ 12 px) and no larger than life, the same for every bench (#277).
  useEffect(() => {
    const fit = () => flow.fitView({ padding: 0.08, minZoom: 0.93, maxZoom: 1, duration: 0 });
    const id = window.setTimeout(fit, 0);
    // Webfonts land after this first fit: card heights grow by a wrapped line, React Flow
    // re-measures the nodes, and the fit would stay computed from the fallback metrics — the
    // viewport sits off by a line until the view changes (#645). One refit once fonts settle.
    let alive = true;
    document.fonts.ready.then(() => {
      if (alive) requestAnimationFrame(fit);
    });
    return () => {
      alive = false;
      window.clearTimeout(id);
    };
  }, [flow, view.id]);

  const onDragOver = (e: DragEvent) => {
    if (editable && e.dataTransfer.types.includes(LIBRARY_MIME)) {
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
    }
  };
  const onDrop = (e: DragEvent) => {
    const raw = e.dataTransfer.getData(LIBRARY_MIME);
    if (!editable || !raw || !onDropItem) return;
    e.preventDefault();
    const at = flow.screenToFlowPosition({ x: e.clientX, y: e.clientY });
    onDropItem(JSON.parse(raw) as LibraryItem, { x: Math.round(at.x), y: Math.round(at.y) });
  };

  const onNodeClick = (_: unknown, n: Node) =>
    select({ kind: view.instruments.some((i) => i.name === n.id) ? "instrument" : "part", id: n.id });

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      onNodesChange={onNodesChange}
      onNodeClick={onNodeClick}
      onEdgeClick={(_, e) => select({ kind: "wire", id: Number(e.id.slice(5)) })}
      onPaneClick={() => select(null)}
      onConnect={onConnect}
      onBeforeDelete={onBeforeDelete}
      onDragOver={onDragOver}
      onDrop={onDrop}
      isValidConnection={(c) => c.source !== c.target || c.sourceHandle !== c.targetHandle}
      connectionMode={ConnectionMode.Loose}
      nodesConnectable={editable}
      nodesDraggable={!!onOps}
      deleteKeyCode={editable ? ["Delete", "Backspace"] : null}
      connectionLineStyle={{ stroke: "var(--primary)", strokeWidth: 2 }}
      zoomOnDoubleClick={false}
      colorMode={theme}
      minZoom={0.2}
      maxZoom={2.5}
      proOptions={{ hideAttribution: true }}
      aria-label={t("workbench.canvas")}
    >
      <Background variant={BackgroundVariant.Dots} gap={20} size={1.2} color="var(--border)" />
      <Controls showInteractive={false} position="bottom-left" />
      <OverflowMiniMap />
    </ReactFlow>
  );
}

/**
 * The minimap, only while some node lies outside the view (#1252): a bench that fits needs no
 * map, and a map drawn over a fitted bench covers its lower-right node once the bottom panel
 * opens and the canvas gets shorter.
 */
function OverflowMiniMap() {
  const outside = useStore((s) => {
    const [tx, ty, zoom] = s.transform;
    for (const n of s.nodeLookup.values()) {
      const w = n.measured.width ?? 0;
      const h = n.measured.height ?? 0;
      const x = n.internals.positionAbsolute.x * zoom + tx;
      const y = n.internals.positionAbsolute.y * zoom + ty;
      if (x < 0 || y < 0 || x + w * zoom > s.width + 0.5 || y + h * zoom > s.height + 0.5) return true;
    }
    return false;
  });
  if (!outside) return null;
  return <MiniMap pannable zoomable position="bottom-right" nodeStrokeWidth={2} className="!border !rounded-md" />;
}

export function Canvas(props: CanvasProps) {
  return (
    <ReactFlowProvider>
      <CanvasInner {...props} />
    </ReactFlowProvider>
  );
}
