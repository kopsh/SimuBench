import { Background, BackgroundVariant, ConnectionMode, Controls, type Edge, Handle, type Node, type NodeProps, Position, ReactFlow, ReactFlowProvider } from "@xyflow/react";
import "@xyflow/react/dist/base.css";
import { Info } from "lucide-react";
import { memo, useDeferredValue, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { InstrumentInfo, InstrumentInternals, InternalComponent, SessionInfo } from "@/api/client";
import { useEvents } from "@/api/events";
import { useInternals } from "@/api/hooks";
import { ErrorState } from "@/components/app/StateViews";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import { channelColor } from "@/lib/channels";
import { displayUnit, formatEng } from "@/lib/format";
import { useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";
import { labelOf, numeric, stateMap } from "../model";
import { type GraphNode, NODE_WIDTH, type PortRow, buildGraph, handleTop } from "./layout";

type ComponentData = { node: Extract<GraphNode, { kind: "component" }>; selected: boolean };
type GroupData = { node: Extract<GraphNode, { kind: "group" }> };
type PortData = { node: Extract<GraphNode, { kind: "port" }> };

/** Read-only view of how an instrument is built from components (issue 20). */
export function InternalsView({ session, inst }: { session: SessionInfo; inst: InstrumentInfo }) {
  const version = useDeferredValue(useEvents((s) => s.version));
  const query = useInternals(session.id, inst.name, version);
  if (query.error) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  if (!query.data) return <Skeleton className="m-3 h-[calc(100%-1.5rem)]" />;
  return <InternalsPanel view={query.data} inst={inst} />;
}

/** The graph and the details of the selected component, from data already loaded. */
export function InternalsPanel({ view, inst }: { view: InstrumentInternals; inst: InstrumentInfo }) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState<Set<number>>(() => new Set([1]));
  const [selected, setSelected] = useState<string | null>(null);
  const comp = view.components.find((c) => c.name === selected) ?? null;

  const channels = Array.from({ length: view.channels }, (_, i) => i + 1);
  const grouped = view.channels > 1 && view.components.some((c) => c.channel != null);
  const toggle = (n: number) =>
    setExpanded((s) => {
      const next = new Set(s);
      if (next.has(n)) next.delete(n);
      else next.add(n);
      return next;
    });

  return (
    <div className="flex h-full min-h-0">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex flex-wrap items-center gap-3 border-b px-3 py-2">
          <div className="text-muted-foreground flex min-w-0 flex-1 items-start gap-1.5 text-xs">
            <Info className="mt-0.5 size-3.5 shrink-0" />
            <span>{t("internals.hint")}</span>
          </div>
          {grouped && (
            <div role="group" aria-label={t("internals.channels")} className="flex items-center gap-1">
              {channels.map((n) => (
                <Button
                  key={n}
                  size="sm"
                  variant={expanded.has(n) ? "secondary" : "ghost"}
                  className="num h-7 px-2 text-xs"
                  aria-pressed={expanded.has(n)}
                  onClick={() => toggle(n)}
                  style={{ color: channelColor(n) }}
                >
                  CH{n}
                </Button>
              ))}
              <Button
                size="sm"
                variant="ghost"
                className="h-7 text-xs"
                onClick={() => setExpanded(expanded.size === channels.length ? new Set() : new Set(channels))}
              >
                {expanded.size === channels.length ? t("internals.collapseAll") : t("internals.expandAll")}
              </Button>
            </div>
          )}
        </div>
        <div className="min-h-0 flex-1" data-testid="internals-graph">
          <ReactFlowProvider>
            <Graph view={view} expanded={expanded} selected={selected} onSelect={setSelected} onExpand={toggle} />
          </ReactFlowProvider>
        </div>
      </div>
      <aside className="bg-sidebar w-72 shrink-0 border-l" aria-label={t("workbench.inspector")}>
        <ScrollArea className="h-full">
          {comp ? (
            <ComponentDetails comp={comp} inst={inst} />
          ) : (
            <p className="text-muted-foreground p-4 text-xs">{t("internals.select")}</p>
          )}
        </ScrollArea>
      </aside>
    </div>
  );
}

const nodeTypes = {
  component: memo(ComponentNode),
  group: memo(GroupNode),
  port: memo(PortNode),
};

function Graph({
  view,
  expanded,
  selected,
  onSelect,
  onExpand,
}: {
  view: InstrumentInternals;
  expanded: Set<number>;
  selected: string | null;
  onSelect: (name: string | null) => void;
  onExpand: (channel: number) => void;
}) {
  const { t } = useTranslation();
  const theme = useTheme((s) => s.resolved);
  const graph = useMemo(() => buildGraph(view, expanded), [view, expanded]);
  const nodes: Node[] = graph.nodes.map((n) => ({
    id: n.id,
    type: n.kind,
    position: { x: n.x, y: n.y },
    data: n.kind === "component" ? { node: n, selected: n.comp.name === selected } : { node: n },
    draggable: false,
    connectable: false,
    selectable: n.kind !== "port",
  }));
  const edges: Edge[] = graph.edges.map((e) => ({
    ...e,
    type: "smoothstep",
    focusable: false,
    selectable: false,
    style: { stroke: "var(--muted-foreground)", strokeWidth: 1.25 },
  }));
  return (
    <ReactFlow
      // A new layout (a channel expanded) fits the view again.
      key={[...expanded].sort().join(",")}
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      onNodeClick={(_, n) => {
        if (n.type === "component") onSelect((n.data as ComponentData).node.comp.name);
        else if (n.type === "group") onExpand((n.data as GroupData).node.channel);
      }}
      onPaneClick={() => onSelect(null)}
      connectionMode={ConnectionMode.Loose}
      nodesConnectable={false}
      nodesDraggable={false}
      edgesFocusable={false}
      deleteKeyCode={null}
      zoomOnDoubleClick={false}
      fitView
      fitViewOptions={{ padding: 0.15, maxZoom: 1.2 }}
      colorMode={theme}
      minZoom={0.2}
      maxZoom={2}
      proOptions={{ hideAttribution: true }}
      aria-label={t("internals.title")}
    >
      <Background variant={BackgroundVariant.Dots} gap={20} size={1.2} color="var(--border)" />
      <Controls showInteractive={false} position="bottom-left" />
    </ReactFlow>
  );
}

function typeLabel(t: (k: string, o?: Record<string, unknown>) => string, type: string): string {
  return t(`internals.types.${type}`, { defaultValue: type });
}

function ComponentNode({ data }: NodeProps<Node<ComponentData>>) {
  const { t } = useTranslation();
  const { node, selected } = data;
  const c = node.comp;
  const index = (row: PortRow) => node.rows.filter((r) => r.side === row.side).indexOf(row);
  return (
    <div
      className={cn(
        "bg-card text-card-foreground relative rounded-lg border shadow-sm",
        selected && "ring-primary ring-2 shadow-md",
      )}
      style={{ width: NODE_WIDTH.component, height: node.height }}
      data-testid="internal-component"
      data-name={c.name}
    >
      {c.channel != null && <div className="absolute inset-y-0 left-0 w-1 rounded-l-lg" style={{ background: channelColor(c.channel) }} aria-hidden />}
      <div className="border-b px-3 py-1.5" style={{ height: 40 }}>
        <div className="truncate text-[13px] leading-tight font-medium">{typeLabel(t, c.type)}</div>
        <div className="text-muted-foreground num truncate text-[11px] leading-tight">{c.name}</div>
      </div>
      {node.rows.map((row) => {
        const top = handleTop(index(row));
        const left = row.side === "left";
        return (
          <div key={`${row.side}:${row.name}`}>
            <Handle
              id={row.name}
              type={left ? "target" : "source"}
              position={left ? Position.Left : Position.Right}
              isConnectable={false}
              style={{ top, width: 8, height: 8, borderWidth: 1.5, borderColor: "var(--muted-foreground)", background: "var(--card)" }}
            />
            <span
              className="num text-muted-foreground pointer-events-none absolute text-[10px] leading-none"
              style={{ top, transform: "translateY(-50%)", ...(left ? { left: 10 } : { right: 10 }) }}
            >
              {row.name}
            </span>
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
      className="bg-muted/60 hover:bg-muted relative flex cursor-pointer flex-col justify-center rounded-lg border border-dashed px-3"
      style={{ width: NODE_WIDTH.group, height: node.height }}
      title={t("internals.expand")}
      data-testid="internal-group"
    >
      <Handle id="in" type="target" position={Position.Left} isConnectable={false} style={{ width: 8, height: 8, background: "var(--card)" }} />
      <div className="num text-[13px] font-semibold" style={{ color: channelColor(node.channel) }}>
        CH{node.channel}
      </div>
      <div className="text-muted-foreground text-[11px]">
        {t("internals.group", { count: node.count })} · {t("internals.expand")}
      </div>
      <Handle id="out" type="source" position={Position.Right} isConnectable={false} style={{ width: 8, height: 8, background: "var(--card)" }} />
    </div>
  );
}

function PortNode({ data }: NodeProps<Node<PortData>>) {
  const { t } = useTranslation();
  const { node } = data;
  const out = node.direction === "out";
  const color = node.medium === "reference" ? "var(--medium-reference)" : channelColor(node.channel);
  return (
    <div
      className="bg-background num flex items-center justify-center rounded-full border-2 text-[11px] font-semibold"
      style={{ width: NODE_WIDTH.port, height: node.height, borderColor: color }}
      title={t("internals.panelPort")}
      data-testid="internal-port"
    >
      {node.name}
      <Handle id="p" type={out ? "target" : "source"} position={out ? Position.Left : Position.Right} isConnectable={false} style={{ width: 8, height: 8, background: color, borderColor: color }} />
    </div>
  );
}

function ComponentDetails({ comp, inst }: { comp: InternalComponent; inst: InstrumentInfo }) {
  const { t } = useTranslation();
  const vars = stateMap(inst);
  return (
    <div className="space-y-4 p-3" data-testid="internal-details">
      <div>
        <div className="text-sm font-semibold">{typeLabel(t, comp.type)}</div>
        <div className="text-muted-foreground num text-xs">
          {comp.name} · {comp.type}
        </div>
        <div className="mt-1 text-xs">
          {comp.channel != null ? (
            <span className="num font-semibold" style={{ color: channelColor(comp.channel) }}>
              CH{comp.channel}
            </span>
          ) : (
            <span className="text-muted-foreground">{t("internals.whole")}</span>
          )}
        </div>
        {comp.doc && <p className="text-muted-foreground mt-2 text-xs leading-relaxed">{comp.doc}</p>}
      </div>
      <section>
        <h3 className="text-muted-foreground mb-1.5 text-[11px] font-medium tracking-wide uppercase">{t("internals.params")}</h3>
        {comp.params.length === 0 ? (
          <p className="text-muted-foreground text-xs">{t("internals.noParams")}</p>
        ) : (
          <dl className="space-y-1.5">
            {comp.params.map((p) => {
              const bound = p.binding ? vars.get(p.binding) : undefined;
              return (
                <div key={p.name} className="text-xs">
                  <div className="flex items-baseline justify-between gap-2">
                    <dt className="num text-muted-foreground truncate">{p.name}</dt>
                    <dd className="num truncate font-medium">{formatParam(p.value, p.unit)}</dd>
                  </div>
                  {p.binding && (
                    <div className="text-primary text-[11px]">
                      {t("internals.follows", { label: bound ? `${labelOf(bound)} (${p.binding})` : p.binding })}
                    </div>
                  )}
                </div>
              );
            })}
          </dl>
        )}
      </section>
      <section>
        <h3 className="text-muted-foreground mb-1.5 text-[11px] font-medium tracking-wide uppercase">{t("internals.ports")}</h3>
        <dl className="space-y-1">
          {comp.ports.map((p) => (
            <div key={p.name} className="flex items-baseline justify-between gap-2 text-xs">
              <dt className="num">{p.name}</dt>
              <dd className="text-muted-foreground">
                {t(`internals.direction.${p.direction}`, { defaultValue: p.direction })} · {t(`internals.medium.${p.medium}`, { defaultValue: p.medium })}
              </dd>
            </div>
          ))}
        </dl>
      </section>
    </div>
  );
}

const SI = new Set(["V", "A", "Hz", "OHM", "F", "S", "s", "m", "W"]);

function formatParam(value: unknown, unit?: string | null): string {
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number" || value === "INF" || value === "-INF") {
    const v = numeric(value);
    if (!Number.isFinite(v)) return String(value);
    // SI units take a prefix (200 MHz); others (ppm, dB) read better without one.
    if (unit && SI.has(unit)) return formatEng(v, displayUnit(unit), 4);
    if (!unit) return formatEng(v, "", 4);
    return `${Number(v.toPrecision(6))} ${unit}`;
  }
  if (value == null) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
