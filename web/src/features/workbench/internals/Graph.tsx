/**
 * The internals graph (issue 20): the ReactFlow canvas with its nodes and edges, drawn where
 * layout.ts's columns put them. The panel (./InternalsPanel) owns the state and renders it; this
 * file holds no state of its own beyond the viewport the canvas keeps.
 */
import {
  Background,
  BackgroundVariant,
  BaseEdge,
  ConnectionMode,
  Controls,
  type Edge,
  type EdgeProps,
  EdgeText,
  Handle,
  MiniMap,
  type Node,
  type NodeProps,
  Position,
  ReactFlow,
  getSmoothStepPath,
  useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/base.css";
import { Activity } from "lucide-react";
import { memo, useEffect, useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";
import type { InstrumentInternals, StateVarInfo } from "@/api/client";
import { blockLabel } from "@/lib/component";
import { channelColor } from "@/lib/channels";
import { prefersReducedMotion } from "@/lib/motion";
import { useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";
import { type MiniSignal, flowTag, miniPath } from "./probe";
import {
  type GraphEdge,
  type GraphNode,
  NODE_WIDTH,
  REF_HANDLE_TOP,
  buildGraph,
} from "./layout";
import { pickedChannel, type Trace } from "./trace";

type ComponentData = {
  node: Extract<GraphNode, { kind: "component" }>;
  selected: boolean;
  /** On the lit chain (#739); dim: something else is lit. */
  lit: boolean;
  dim: boolean;
  /** Open the internal probe on one of its outputs (#953); absent where there is no session to read. */
  onProbe?: (port: string) => void;
  /** The port the probe shows now, '<component>.<port>'. */
  probing: string | null;
  /** The signal at each of its outputs, drawn on the port's row; absent while unknown. */
  signals?: ReadonlyMap<string, MiniSignal>;
};
type GroupData = { node: Extract<GraphNode, { kind: "group" }>; dim: boolean };
type PortData = { node: Extract<GraphNode, { kind: "port" }>; dim: boolean };
type LabelData = { node: Extract<GraphNode, { kind: "label" }>; dim: boolean };

const nodeTypes = {
  component: memo(ComponentNode),
  // Not "group": xyflow styles its own built-in group type (a 150 px bordered box under ours, #1234).
  channel: memo(GroupNode),
  port: memo(PortNode),
  label: memo(LabelNode),
};

/**
 * A signal wire inside the instrument (signal-chain.md 1.2): thin when analog, a hollow double line
 * when it carries a sample stream — the way a block diagram draws a data bus — so the two are told
 * apart by shape and by their label, not by colour alone. The label sits on the run leaving the
 * source, between the columns, where no vertical step or dependency label crowds it.
 */
function FlowEdge({
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  style,
  label,
  labelStyle,
  labelBgStyle,
  data,
}: EdgeProps) {
  const [path] = getSmoothStepPath({ sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition });
  const { medium, lit } = (data ?? {}) as { medium?: string; lit?: boolean };
  const digital = medium === "samples";
  return (
    <>
      <BaseEdge path={path} style={digital ? { ...style, strokeWidth: 4.5 } : style} />
      {digital && <path d={path} fill="none" stroke="var(--background)" strokeWidth={1.75} pointerEvents="none" data-digital="" />}
      {/* On the lit chain the signal is seen to move, source to target: beads on an analog wire,
          words travelling inside a sample stream's double line. Still under reduced motion. */}
      {lit && (
        <path
          d={path}
          className="internals-flow"
          fill="none"
          stroke={digital ? "var(--primary)" : "var(--foreground)"}
          strokeWidth={digital ? 1.75 : 2}
          strokeLinecap={digital ? "butt" : "round"}
          strokeDasharray={digital ? "5 11" : "0.1 15.9"}
          pointerEvents="none"
        />
      )}
      {typeof label === "string" && (
        <EdgeText
          x={sourceX + 10 + textWidth(label) / 2}
          y={sourceY}
          label={label}
          labelStyle={labelStyle}
          labelShowBg
          labelBgStyle={labelBgStyle}
          labelBgPadding={[3, 2]}
          labelBgBorderRadius={3}
        />
      )}
    </>
  );
}

/**
 * What a component uses (#729): dashed, named by the parameter. The name sits at the user's end —
 * a wire's label sits at its source's end, so the two never share a place between two columns.
 */
function RefEdge({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, style, label, labelStyle, labelBgStyle }: EdgeProps) {
  const [path] = getSmoothStepPath({ sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition });
  // Straight up within a column: the name sits on the line, half-way.
  const upright = sourcePosition === Position.Top;
  return (
    <>
      <BaseEdge path={path} style={style} />
      {typeof label === "string" && (
        <EdgeText
          x={upright ? targetX : targetX - 10 - textWidth(label) / 2}
          y={upright ? (sourceY + targetY) / 2 : targetY}
          label={label}
          labelStyle={labelStyle}
          labelShowBg
          labelBgStyle={labelBgStyle}
          labelBgPadding={[3, 2]}
          labelBgBorderRadius={3}
        />
      )}
    </>
  );
}

/** A label's width at the edge labels' size, near enough to place it: full-width characters count double. */
function textWidth(text: string): number {
  let w = 0;
  for (const ch of text) w += ch.charCodeAt(0) > 0x2e80 ? 10.5 : 5.6;
  return w;
}

const edgeTypes = { flow: FlowEdge, ref: RefEdge };

export function Graph({
  view,
  vars,
  expanded,
  selected,
  lit,
  onSelect,
  onExpand,
  onProbe,
  probing,
  signals,
  overview,
  onOverflows,
  opened,
}: {
  signals?: ReadonlyMap<string, MiniSignal>;
  /** The overview map is shown (asked for, and part of the graph is out of view). */
  overview: boolean;
  /** Whether part of the graph lies outside the view, after each move. */
  onOverflows: (outside: boolean) => void;
  /** A channel just expanded: its first block is brought into view once the new layout opens. */
  opened?: number | null;
  view: InstrumentInternals;
  vars: Map<string, StateVarInfo>;
  expanded: Set<number>;
  selected: string | null;
  lit: Trace | null;
  onSelect: (name: string | null) => void;
  onExpand: (channel: number) => void;
  onProbe?: (port: string) => void;
  probing: string | null;
}) {
  const { t } = useTranslation();
  const theme = useTheme((s) => s.resolved);
  const graph = useMemo(() => buildGraph(view, expanded, (flow) => textWidth(flowTag(t, flow))), [view, expanded, t]);
  const rf = useReactFlow();
  const wrap = useRef<HTMLDivElement>(null);
  // The view opens readable, not merely complete: the whole graph when it fits at a size its text
  // can be read at, otherwise at that size from the left — where the signal comes in — and the
  // overview map says where the rest is.
  const home = () => {
    const box = wrap.current?.getBoundingClientRect();
    if (!box?.width || !box.height) return null;
    const b = boundsOf(graph.nodes);
    // On the left a gutter for the zoom buttons: no block opens under them (#1234).
    const fit = Math.min((box.width - CONTROLS_GUTTER - VIEW_PAD) / b.width, (box.height - 2 * VIEW_PAD) / b.height);
    const zoom = Math.min(MAX_OPEN_ZOOM, Math.max(fit, READABLE_ZOOM));
    const along = (size: number, from: number, extent: number, lead: number) =>
      extent * zoom <= size - lead - VIEW_PAD ? lead + (size - lead - VIEW_PAD - extent * zoom) / 2 - from * zoom : lead - from * zoom;
    return { x: along(box.width, b.x, b.width, CONTROLS_GUTTER), y: along(box.height, b.y, b.height, VIEW_PAD), zoom };
  };
  /** Whether part of the graph lies outside the view as it stands: only then is the overview map worth its corner. */
  const outside = (v: { x: number; y: number; zoom: number }) => {
    const box = wrap.current?.getBoundingClientRect();
    if (!box?.width) return false;
    const b = boundsOf(graph.nodes);
    return b.x * v.zoom + v.x < 0 || b.y * v.zoom + v.y < 0 || (b.x + b.width) * v.zoom + v.x > box.width || (b.y + b.height) * v.zoom + v.y > box.height;
  };
  /** Bring a block into view if it is not (found by search, or chosen in the details): the least move that shows it whole. */
  const reveal = (name: string, animate: boolean) => {
    const box = wrap.current?.getBoundingClientRect();
    const c = view.components.find((x) => x.name === name);
    const n = graph.nodes.find((x) => x.id === `c:${name}`) ?? graph.nodes.find((x) => x.id === `g:${c?.channel}`);
    if (!box?.width || !n || !rf.viewportInitialized) return;
    const v = rf.getViewport();
    const shift = (pos: number, extent: number, size: number, at: number) => {
      const lo = pos * v.zoom + at;
      const hi = lo + extent * v.zoom;
      if (lo < VIEW_PAD) return at + VIEW_PAD - lo;
      if (hi > size - VIEW_PAD) return at - (hi - (size - VIEW_PAD));
      return at;
    };
    const x = shift(n.x, NODE_WIDTH.component, box.width, v.x);
    const y = shift(n.y, n.height, box.height, v.y);
    if (x !== v.x || y !== v.y) void rf.setViewport({ x, y, zoom: v.zoom }, { duration: animate && !prefersReducedMotion() ? 260 : 0 });
  };
  useEffect(() => {
    if (selected) reveal(selected, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only when the selection changes
  }, [selected]);
  // Part of the graph may lie outside the view, and a browser brings an element into view by
  // scrolling its clipped ancestors: a port reached with Tab, a find-in-page hit, a script's
  // scrollIntoView. Scrolling the pane would shift the graph under its own overlays, so the scroll
  // is turned into the pan it stands for.
  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const onScroll = (e: Event) => {
      const pane = e.target as HTMLElement;
      const { scrollLeft: dx, scrollTop: dy } = pane;
      if (!dx && !dy) return;
      pane.scrollTo(0, 0);
      if (!rf.viewportInitialized) return;
      const v = rf.getViewport();
      void rf.setViewport({ x: v.x - dx, y: v.y - dy, zoom: v.zoom });
    };
    el.addEventListener("scroll", onScroll, true);
    return () => el.removeEventListener("scroll", onScroll, true);
  }, [rf]);
  const comps = useMemo(
    () => new Map(view.components.map((c) => [c.name, c])),
    [view],
  );
  const isLit = (n: GraphNode) => {
    if (!lit) return false;
    if (n.kind === "component") return lit.nodes.has(n.comp.name);
    if (n.kind === "port") return lit.nodes.has(`p:${n.name}`);
    if (n.kind === "group")
      return view.components.some(
        (c) => c.channel === n.channel && lit.nodes.has(c.name),
      );
    return false;
  };
  const nodes: Node[] = graph.nodes.map((n) => {
    const dim = !!lit && !isLit(n);
    return {
      id: n.id,
      type: n.kind === "group" ? "channel" : n.kind,
      position: { x: n.x, y: n.y },
      // The layout knows every node's size: the overview map draws from it without waiting for a measurement.
      initialWidth: NODE_WIDTH[n.kind],
      initialHeight: n.height,
      data:
        n.kind === "component"
          ? { node: n, selected: n.comp.name === selected, lit: !dim && !!lit, dim, onProbe, probing, signals }
          : { node: n, dim },
      draggable: false,
      connectable: false,
      selectable: n.kind !== "port",
    };
  });
  const base = { stroke: "var(--muted-foreground)", strokeWidth: 1.25 };
  // Parallel wires between the same two blocks that carry the same thing (a converter's four
  // channels) are labelled once, on the first.
  const labelled = new Set<string>();
  const wireLabel = (source: string, target: string, flow: NonNullable<GraphEdge["flow"]>) => {
    const tag = flowTag(t, flow);
    const key = `${source}>${target}>${tag}`;
    if (labelled.has(key)) return undefined;
    labelled.add(key);
    return tag;
  };
  const edges: Edge[] = graph.edges.map(({ kind, label, links, flow, ...e }) => {
    const on = !!lit && links.some((l) => lit.links.has(l));
    const style = {
      ...base,
      ...(kind === "ref" ? { strokeDasharray: "5 4" } : {}),
      ...(on
        ? { stroke: "var(--primary)", strokeWidth: 2 }
        : lit
          ? { opacity: 0.2 }
          : kind === "ref"
            ? { opacity: 0.8 }
            : {}),
    };
    const text = { labelStyle: { fontSize: 10.5, fill: "var(--muted-foreground)" }, labelBgStyle: { fill: "var(--background)" } };
    return {
      ...e,
      // A signal wire is solid — thin when analog, a double line when it carries a sample stream —
      // and says which in words (signal-chain.md 1.2); what a component uses is dashed and named by
      // its parameter (#729).
      type: flow ? "flow" : kind === "ref" ? "ref" : "smoothstep",
      focusable: false,
      selectable: false,
      zIndex: on ? 1 : 0,
      ...(kind === "ref" ? { label, ...text } : flow ? { label: wireLabel(e.source, e.target, flow), ...text } : {}),
      style,
      data: { kind, lit: on, medium: flow?.medium },
    };
  });
  // A channel picked by a setting (a trigger source) has no wire or ref to draw: while the chain
  // is lit, a dotted line names the setting and says which channel it picks now (#739).
  const drawn = new Set(graph.edges.flatMap((e) => e.links));
  const nodeOf = (name: string) => {
    const c = comps.get(name);
    return c && c.channel != null && !expanded.has(c.channel)
      ? `g:${c.channel}`
      : `c:${name}`;
  };
  for (const l of lit?.links ?? []) {
    if (drawn.has(l)) continue;
    const [a, b] = l.split(">");
    const sel = view.selects?.find((s) => s.component === b);
    if (!sel || !comps.has(a)) continue;
    const source = nodeOf(a);
    const ch = comps.get(a)?.channel ?? null;
    const picked = pickedChannel(vars.get(sel.setting)?.value);
    const current = ch != null && picked === ch;
    edges.push({
      id: `select:${l}`,
      source,
      sourceHandle: source.startsWith("g:") ? "out" : "ref-out",
      target: nodeOf(b),
      targetHandle: "ref-in",
      type: "smoothstep",
      focusable: false,
      selectable: false,
      zIndex: 1,
      label: current
        ? t("internals.picks", { setting: sel.setting })
        : t("internals.picksOther", {
            setting: sel.setting,
            value: String(vars.get(sel.setting)?.value ?? "—"),
          }),
      labelStyle: { fontSize: 10, fill: "var(--muted-foreground)" },
      labelBgStyle: { fill: "var(--background)" },
      style: {
        stroke: current ? "var(--primary)" : "var(--muted-foreground)",
        strokeWidth: 1.5,
        strokeDasharray: "1 4",
        strokeLinecap: "round",
      },
      data: { kind: "select", lit: current },
    });
  }
  return (
    <div className="flex h-full w-full flex-col">
    <div
      ref={wrap}
      className="min-h-0 w-full flex-1"
      // A block or port reached with Tab may lie left of or above the view, where no scroll reaches: pan to it.
      onFocusCapture={(e) => {
        const name = (e.target as HTMLElement).closest<HTMLElement>("[data-name]")?.dataset.name;
        if (name) reveal(name, true);
      }}
    >
    <ReactFlow
      // A new layout (a channel expanded) fits the view again.
      key={[...expanded].sort().join(",")}
      onInit={(flow) => {
        const start = home();
        if (!start) return;
        void flow.setViewport(start);
        onOverflows(outside(start));
        if (selected) reveal(selected, false);
        else if (opened != null) {
          const first = view.components.find((c) => c.channel === opened);
          if (first) reveal(first.name, false);
        }
      }}
      onMoveEnd={(_, v) => onOverflows(outside(v))}
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      onNodeClick={(_, n) => {
        if (n.type === "component")
          onSelect((n.data as ComponentData).node.comp.name);
        else if (n.type === "channel")
          onExpand((n.data as GroupData).node.channel);
      }}
      onPaneClick={() => onSelect(null)}
      connectionMode={ConnectionMode.Loose}
      nodesConnectable={false}
      nodesDraggable={false}
      edgesFocusable={false}
      deleteKeyCode={null}
      zoomOnDoubleClick={false}
      fitViewOptions={{ padding: 0.1, maxZoom: MAX_OPEN_ZOOM }}
      colorMode={theme}
      minZoom={0.2}
      maxZoom={2}
      proOptions={{ hideAttribution: true }}
      aria-label={t("internals.title")}
    >
      <Background
        variant={BackgroundVariant.Dots}
        gap={20}
        size={1.2}
        color="var(--border)"
      />
      <Controls showInteractive={false} position="bottom-left" />
    </ReactFlow>
    </div>
    {/* The overview map in a strip of its own under the diagram, not over a corner of it (#1234). */}
    {overview && (
      <div className="bg-card relative h-24 shrink-0 border-t" data-testid="internals-overview-strip">
        <MiniMap
          pannable
          zoomable
          position="bottom-right"
          ariaLabel={t("internals.overview")}
          style={{ width: 320, height: 84, margin: 6 }}
          bgColor="var(--card)"
          maskColor="color-mix(in oklab, var(--foreground) 9%, transparent)"
          nodeStrokeWidth={0}
          nodeColor={(n) =>
            n.type === "label"
              ? "transparent"
              : (n.data as { dim?: boolean }).dim
                ? "var(--border)"
                : lit || n.id === `c:${selected}`
                  ? "var(--primary)"
                  : "color-mix(in oklab, var(--muted-foreground) 65%, transparent)"
          }
          nodeBorderRadius={6}
        />
      </div>
    )}
    </div>
  );
}

/** The smallest size the view opens at: a block's name (13 px) and a port row's label (12.5 px)
 * show at 10 px or more (#1234, #1240). */
const READABLE_ZOOM = 0.8;
const MAX_OPEN_ZOOM = 1.15;
const VIEW_PAD = 24;
/** The zoom buttons' column at the bottom left (15 px in, 26 px wide) and a margin. */
const CONTROLS_GUTTER = 52;

/** The box around every node of the graph. */
function boundsOf(nodes: readonly GraphNode[]): { x: number; y: number; width: number; height: number } {
  const x = Math.min(...nodes.map((n) => n.x));
  const y = Math.min(...nodes.map((n) => n.y));
  const right = Math.max(...nodes.map((n) => n.x + NODE_WIDTH[n.kind]));
  const bottom = Math.max(...nodes.map((n) => n.y + n.height));
  return { x, y, width: Math.max(1, right - x), height: Math.max(1, bottom - y) };
}

/** Where a port row's thumbnail starts, and what it leaves for the port's name at the right. */
const MINI = { left: 44, right: 58, height: 22 } as const;

function ComponentNode({ data }: NodeProps<Node<ComponentData>>) {
  const { t } = useTranslation();
  const { node, selected, lit, dim, onProbe, probing } = data;
  const c = node.comp;
  return (
    <div
      className={cn(
        "bg-card text-card-foreground relative rounded-lg border shadow-sm transition-opacity motion-reduce:transition-none",
        selected && "ring-primary ring-2 shadow-md",
        lit && !selected && "ring-primary/50 ring-1",
        dim && "opacity-35",
      )}
      style={{ width: NODE_WIDTH.component, height: node.height }}
      data-testid="internal-component"
      data-name={c.name}
      data-lit={lit || selected ? "" : undefined}
    >
      {c.channel != null && (
        <div
          className="absolute inset-y-0 left-0 w-1 rounded-l-lg"
          style={{ background: channelColor(c.channel) }}
          aria-hidden
        />
      )}
      <div className="border-b px-3 py-1.5" style={{ height: 40 }}>
        <div className="truncate text-[13px] leading-tight font-medium" data-part="block-name">
          {blockLabel(c)}
        </div>
        <div className="text-muted-foreground num truncate text-[11px] leading-tight">
          {c.name}
        </div>
      </div>
      {/* Where a dependency arrives (what this component uses) and leaves (who uses it), #729. */}
      <Handle
        id="ref-in"
        type="target"
        position={Position.Left}
        isConnectable={false}
        style={{
          top: REF_HANDLE_TOP,
          width: 6,
          height: 6,
          borderWidth: 1,
          borderStyle: "dashed",
          borderColor: "var(--muted-foreground)",
          background: "var(--card)",
        }}
      />
      <Handle
        id="ref-out"
        type="source"
        position={Position.Right}
        isConnectable={false}
        style={{
          top: REF_HANDLE_TOP,
          width: 6,
          height: 6,
          borderWidth: 1,
          borderStyle: "dashed",
          borderColor: "var(--muted-foreground)",
          background: "var(--card)",
        }}
      />
      {/* A dependency inside one column (#959): out of the used block's top, into its user's foot. */}
      <Handle id="ref-up" type="source" position={Position.Top} isConnectable={false} style={{ width: 6, height: 6, borderWidth: 1, borderStyle: "dashed", borderColor: "var(--muted-foreground)", background: "var(--card)", opacity: 0 }} />
      <Handle id="ref-foot" type="target" position={Position.Bottom} isConnectable={false} style={{ width: 6, height: 6, borderWidth: 1, borderStyle: "dashed", borderColor: "var(--muted-foreground)", background: "var(--card)", opacity: 0 }} />
      {node.rows.map((row) => {
        const top = row.top;
        const left = row.side === "left";
        const port = `${c.name}.${row.name}`;
        const mini = row.probe ? data.signals?.get(port) : undefined;
        const miniWidth = NODE_WIDTH.component - MINI.left - MINI.right;
        return (
          <div key={`${row.side}:${row.name}`}>
            <Handle
              id={row.name}
              type={left ? "target" : "source"}
              position={left ? Position.Left : Position.Right}
              isConnectable={false}
              style={{
                top,
                width: 8,
                height: 8,
                borderWidth: 1.5,
                borderColor: "var(--muted-foreground)",
                background: "var(--card)",
              }}
            />
            {row.probe && onProbe ? (
              // An output the internal probe reads (signal-chain.md 1.3): a button, so the keyboard
              // reaches it (Tab, then Enter) as well as the pointer.
              <button
                type="button"
                className={cn(
                  "nodrag nopan num focus-visible:ring-ring group/port absolute flex items-center justify-end gap-1.5 rounded-md py-0.5 pr-1 pl-0.5 text-[12.5px] leading-none outline-none focus-visible:ring-2",
                  probing === port
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:bg-accent hover:text-foreground",
                )}
                data-part="port-name"
                style={{ top, left: MINI.left - 2, right: 6, transform: "translateY(-50%)" }}
                aria-label={t("internals.probe.open", { port: `${blockLabel(c)} · ${row.name}` })}
                aria-pressed={probing === port}
                title={t("internals.probe.open", { port })}
                data-testid="internal-probe-port"
                data-port={port}
                onClick={(e) => {
                  e.stopPropagation();
                  onProbe(port);
                }}
              >
                {/* The signal at this output, as it is now: a line when analog, steps when sampled,
                    a dotted rule when it reads 0. The diagram shows the chain at work without a click. */}
                {mini && (
                  <svg
                    width={miniWidth}
                    height={MINI.height}
                    viewBox={`0 0 ${miniWidth} ${MINI.height}`}
                    className="bg-background shrink-0 rounded-[3px] border"
                    aria-hidden
                    data-testid="internal-mini"
                    data-kind={mini.silent ? "silent" : mini.kind}
                  >
                    {mini.silent ? (
                      <path d={`M3,${MINI.height / 2}H${miniWidth - 3}`} stroke="var(--muted-foreground)" strokeWidth="1" strokeDasharray="1 3" />
                    ) : (
                      <path
                        d={miniPath(mini, miniWidth, MINI.height)}
                        fill="none"
                        stroke={c.channel != null ? channelColor(c.channel) : "var(--primary)"}
                        strokeWidth="1.25"
                        strokeLinejoin={mini.kind === "samples" ? "miter" : "round"}
                      />
                    )}
                  </svg>
                )}
                <span className="flex items-center gap-0.5">
                  {row.name}
                  <Activity className="size-2.5" aria-hidden />
                </span>
              </button>
            ) : (
              <span
                className="num text-muted-foreground pointer-events-none absolute text-[12.5px] leading-none"
                data-part="port-name"
                style={{
                  top,
                  transform: "translateY(-50%)",
                  ...(left ? { left: 10 } : { right: 10 }),
                }}
              >
                {row.name}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}

function GroupNode({ data }: NodeProps<Node<GroupData>>) {
  const { t } = useTranslation();
  const { node } = data;
  return (
    <div
      className={cn(
        "bg-muted/60 hover:bg-muted relative flex cursor-pointer flex-col justify-center rounded-lg border border-dashed px-3",
        data.dim && "opacity-35",
      )}
      style={{ width: NODE_WIDTH.group, height: node.height }}
      title={t("internals.expand")}
      data-testid="internal-group"
    >
      <Handle
        id="in"
        type="target"
        position={Position.Left}
        isConnectable={false}
        style={{ width: 8, height: 8, background: "var(--card)" }}
      />
      <div
        className="num text-[13px] font-semibold"
        style={{ color: channelColor(node.channel) }}
      >
        CH{node.channel}
      </div>
      <div className="text-muted-foreground text-[11px]">
        {t("internals.group", { count: node.count })} · {t("internals.expand")}
      </div>
      <Handle
        id="out"
        type="source"
        position={Position.Right}
        isConnectable={false}
        style={{ width: 8, height: 8, background: "var(--card)" }}
      />
    </div>
  );
}

/** The title of the band of components only commands and settings drive (#729). */
function LabelNode({ data }: NodeProps<Node<LabelData>>) {
  const { t } = useTranslation();
  return (
    <div
      className={cn(
        "text-muted-foreground flex flex-col justify-center text-[11px] leading-snug",
        data.dim && "opacity-35",
      )}
      style={{ width: NODE_WIDTH.label, height: data.node.height }}
      data-testid="internal-command-side"
    >
      <div className="text-foreground text-xs font-medium">
        {t("internals.commandSide")}
      </div>
      <div>{t("internals.commandSideHint")}</div>
    </div>
  );
}

function PortNode({ data }: NodeProps<Node<PortData>>) {
  const { t } = useTranslation();
  const { node } = data;
  const out = node.direction === "out";
  const color =
    node.medium === "reference"
      ? "var(--medium-reference)"
      : channelColor(node.channel);
  return (
    <div
      className={cn(
        "bg-background num flex items-center justify-center rounded-full border-2 text-[11px] font-semibold",
        data.dim && "opacity-35",
      )}
      style={{
        width: NODE_WIDTH.port,
        height: node.height,
        borderColor: color,
      }}
      title={t("internals.panelPort")}
      data-testid="internal-port"
    >
      {node.name}
      <Handle
        id="p"
        type={out ? "target" : "source"}
        position={out ? Position.Left : Position.Right}
        isConnectable={false}
        style={{ width: 8, height: 8, background: color, borderColor: color }}
      />
    </div>
  );
}
