import {
  Background,
  BackgroundVariant,
  ConnectionMode,
  Controls,
  MiniMap,
  type Node,
  type NodeChange,
  ReactFlow,
  ReactFlowProvider,
  applyNodeChanges,
  useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/base.css";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { SessionInfo } from "@/api/client";
import { channelColor } from "@/lib/channels";
import { load, save } from "@/lib/storage";
import { useTheme } from "@/lib/theme";
import { accessoryLabel, channelOfRef } from "../model";
import { useWorkbench } from "../store";
import { DutNode, type DutNodeType } from "./DutNode";
import { InstrumentNode, type InstrumentNodeType } from "./InstrumentNode";
import { WireEdge, type WireEdgeType } from "./WireEdge";

const nodeTypes = { instrument: InstrumentNode, dut: DutNode };
const edgeTypes = { wire: WireEdge };

type AnyNode = InstrumentNodeType | DutNodeType;

function split(ref: string): [string, string] {
  const i = ref.lastIndexOf(".");
  return [ref.slice(0, i), ref.slice(i + 1)];
}

function initialPositions(session: SessionInfo): Record<string, { x: number; y: number }> {
  const saved = load<Record<string, { x: number; y: number }>>(`layout:${session.bench_path ?? session.bench_name}`, {});
  const out: Record<string, { x: number; y: number }> = {};
  const names = [...session.instruments.map((i) => i.name), ...session.duts.map((d) => d.name)];
  names.forEach((name, i) => {
    out[name] = saved[name] ?? (session.layout[name] ? { x: session.layout[name].x, y: session.layout[name].y } : { x: i * 320, y: 60 });
  });
  return out;
}

function CanvasInner({ session }: { session: SessionInfo }) {
  const { t } = useTranslation();
  const { selection, select } = useWorkbench();
  const theme = useTheme((s) => s.resolved);
  const flow = useReactFlow();
  const layoutKey = `layout:${session.bench_path ?? session.bench_name}`;
  const [positions, setPositions] = useState(() => initialPositions(session));

  const selectedNet =
    selection?.kind === "wire"
      ? session.wires[selection.id]?.net
      : selection?.kind === "net"
        ? selection.id
        : null;

  const built = useMemo<AnyNode[]>(
    () => [
      ...session.instruments.map<InstrumentNodeType>((inst) => ({
        id: inst.name,
        type: "instrument",
        position: positions[inst.name],
        data: { inst },
      })),
      ...session.duts.map<DutNodeType>((dut) => ({
        id: dut.name,
        type: "dut",
        position: positions[dut.name],
        data: { dut },
      })),
    ],
    [session, positions],
  );
  const [nodes, setNodes] = useState<AnyNode[]>(built);
  const selectedId = selection?.kind === "instrument" || selection?.kind === "dut" ? selection.id : null;
  // Rebuild nodes when the session changes; keep React Flow's own selection unless it differs from
  // the store (selection made in the resource tree), so that clicks are never interrupted.
  useEffect(() => setNodes(built.map((n) => ({ ...n, selected: n.id === useWorkbench.getState().selection?.id }))), [built]);
  useEffect(() => {
    setNodes((ns) => (ns.some((n) => !!n.selected !== (n.id === selectedId)) ? ns.map((n) => ({ ...n, selected: n.id === selectedId })) : ns));
  }, [selectedId]);

  const edges = useMemo<WireEdgeType[]>(
    () =>
      session.wires.map((w) => {
        const [src, sh] = split(w.from);
        const [dst, th] = split(w.to);
        const ch = channelOfRef(session, w.to) ?? channelOfRef(session, w.from);
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
          },
        };
      }),
    [session, selection, selectedNet],
  );

  const onNodesChange = useCallback(
    (changes: NodeChange<AnyNode>[]) => {
      setNodes((ns) => applyNodeChanges(changes, ns));
      const moved = changes.filter((c) => c.type === "position" && c.position && !c.dragging);
      if (moved.length) {
        setPositions((p) => {
          const next = { ...p };
          for (const c of moved) if (c.type === "position" && c.position) next[c.id] = c.position;
          save(layoutKey, next);
          return next;
        });
      }
    },
    [layoutKey],
  );

  useEffect(() => {
    const id = window.setTimeout(() => flow.fitView({ padding: 0.25, duration: 0 }), 0);
    return () => window.clearTimeout(id);
  }, [flow, session.id]);

  const onNodeClick = (_: unknown, n: Node) =>
    select({ kind: session.instruments.some((i) => i.name === n.id) ? "instrument" : "dut", id: n.id });

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
      connectionMode={ConnectionMode.Loose}
      nodesConnectable={false}
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

export function Canvas({ session }: { session: SessionInfo }) {
  return (
    <ReactFlowProvider>
      <CanvasInner session={session} />
    </ReactFlowProvider>
  );
}
