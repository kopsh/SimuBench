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
} from "@xyflow/react";
import "@xyflow/react/dist/base.css";
import { type DragEvent, useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { EditOp, SessionInfo } from "@/api/client";
import { channelColor } from "@/lib/channels";
import { useTheme } from "@/lib/theme";
import { accessoryLabel, channelOfRef } from "../model";
import { useWorkbench } from "../store";
import { DutNode, type DutNodeType } from "./DutNode";
import { InstrumentNode, type InstrumentNodeType, type Issue } from "./InstrumentNode";
import { WireEdge, type WireEdgeType } from "./WireEdge";

const nodeTypes = { instrument: InstrumentNode, dut: DutNode };
const edgeTypes = { wire: WireEdge };

/** MIME type of library items dragged onto the canvas. */
export const LIBRARY_MIME = "application/x-simubench-item";
export type LibraryItem = { kind: "instrument" | "dut"; id: string };

type AnyNode = InstrumentNodeType | DutNodeType;
type Point = { x: number; y: number };

// Where "add" from the library places an item when it is not dropped: the middle of the view.
let viewCenter: (() => Point) | null = null;
export function centerOfView(): Point | null {
  return viewCenter?.() ?? null;
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
}

function split(ref: string): [string, string] {
  const i = ref.lastIndexOf(".");
  return [ref.slice(0, i), ref.slice(i + 1)];
}

function positionsOf(view: SessionInfo, layout: Record<string, Point> | undefined): Record<string, Point> {
  const source = layout ?? view.layout;
  const out: Record<string, Point> = {};
  const names = [...view.instruments.map((i) => i.name), ...view.duts.map((d) => d.name)];
  names.forEach((name, i) => {
    const p = source[name];
    out[name] = p ? { x: Number(p.x) || 0, y: Number(p.y) || 0 } : { x: i * 320, y: 60 };
  });
  return out;
}

function CanvasInner({ view, layout, editable = false, onOps, onDropItem, issues }: CanvasProps) {
  const { t } = useTranslation();
  const { selection, select } = useWorkbench();
  const theme = useTheme((s) => s.resolved);
  const flow = useReactFlow();
  const positions = useMemo(() => positionsOf(view, layout), [view, layout]);

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
        data: { inst, editable, issue: issues?.get(inst.name) },
      })),
      ...view.duts.map<DutNodeType>((dut) => ({
        id: dut.name,
        type: "dut",
        position: positions[dut.name],
        data: { dut, editable, issue: issues?.get(dut.name) },
      })),
    ],
    [view, positions, editable, issues],
  );
  const selectedId = selection?.kind === "instrument" || selection?.kind === "dut" ? selection.id : null;
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
    () =>
      view.wires.map((w) => {
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
            highlighted: !!selectedNet && w.net === selectedNet,
            issue: issues?.get(`wire:${w.index}`),
          },
        };
      }),
    [view, selection, selectedNet, issues],
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
    return () => {
      viewCenter = null;
    };
  }, [flow]);

  useEffect(() => {
    const id = window.setTimeout(() => flow.fitView({ padding: 0.25, duration: 0 }), 0);
    return () => window.clearTimeout(id);
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
    select({ kind: view.instruments.some((i) => i.name === n.id) ? "instrument" : "dut", id: n.id });

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
      <MiniMap pannable zoomable position="bottom-right" nodeStrokeWidth={2} className="!border !rounded-md" />
    </ReactFlow>
  );
}

export function Canvas(props: CanvasProps) {
  return (
    <ReactFlowProvider>
      <CanvasInner {...props} />
    </ReactFlowProvider>
  );
}
