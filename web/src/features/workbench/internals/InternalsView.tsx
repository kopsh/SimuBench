import {
  Background,
  BackgroundVariant,
  ConnectionMode,
  Controls,
  type Edge,
  Handle,
  type Node,
  type NodeProps,
  Position,
  ReactFlow,
  ReactFlowProvider,
} from "@xyflow/react";
import "@xyflow/react/dist/base.css";
import { ArrowRight, Info, Pin, Search } from "lucide-react";
import { type ReactNode, memo, useDeferredValue, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { T } from "@/i18n";
import type {
  InstrumentInfo,
  InstrumentInternals,
  InternalComponent,
  SessionInfo,
  StateVarInfo,
} from "@/api/client";
import { useEvents } from "@/api/events";
import { useInternals } from "@/api/hooks";
import { ErrorState } from "@/components/app/StateViews";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import { channelColor } from "@/lib/channels";
import { displayUnit, formatEng } from "@/lib/format";
import { useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";
import { formatValue, labelOf, numeric, stateMap } from "../model";
import {
  type GraphNode,
  NODE_WIDTH,
  type PortRow,
  REF_HANDLE_TOP,
  buildGraph,
  handleTop,
} from "./layout";
import {
  type ReadingGroup,
  type SearchHit,
  type Trace,
  controlRows,
  nodesTrace,
  operationCommands,
  pathTrace,
  pickedChannel,
  readingGroups,
  search,
  traceOf,
} from "./trace";

type ComponentData = {
  node: Extract<GraphNode, { kind: "component" }>;
  selected: boolean;
  /** On the lit chain (#739); dim: something else is lit. */
  lit: boolean;
  dim: boolean;
};
type GroupData = { node: Extract<GraphNode, { kind: "group" }>; dim: boolean };
type PortData = { node: Extract<GraphNode, { kind: "port" }>; dim: boolean };
type LabelData = { node: Extract<GraphNode, { kind: "label" }>; dim: boolean };

/** Read-only view of how an instrument is built from components (issue 20). */
export function InternalsView({
  session,
  inst,
}: {
  session: SessionInfo;
  inst: InstrumentInfo;
}) {
  const version = useDeferredValue(useEvents((s) => s.version));
  const query = useInternals(session.id, inst.name, version);
  if (query.error)
    return (
      <ErrorState error={query.error} onRetry={() => void query.refetch()} />
    );
  if (!query.data) return <Skeleton className="m-3 h-[calc(100%-1.5rem)]" />;
  return <InternalsPanel view={query.data} inst={inst} />;
}

/** What lights up on the graph: a hovered or pinned row, else the selection's chain, else search hits. */
type Focus = { key: string; trace: Trace } | null;

/** The graph and the details of the selected component, from data already loaded. */
export function InternalsPanel({
  view,
  inst,
  initial,
}: {
  view: InstrumentInternals;
  inst: InstrumentInfo;
  /** Where the view starts (stories): a selected component or a search. */
  initial?: { selected?: string; query?: string };
}) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState<Set<number>>(() => new Set([1]));
  const [selected, setSelected] = useState<string | null>(
    initial?.selected ?? null,
  );
  const [query, setQuery] = useState(initial?.query ?? "");
  const [pinned, setPinned] = useState<Focus>(null);
  const [hover, setHover] = useState<Focus>(null);
  const comp = view.components.find((c) => c.name === selected) ?? null;
  const vars = useMemo(() => stateMap(inst), [inst]);
  const hits = useMemo(
    () =>
      search(view, query, (path) => {
        const v = vars.get(path);
        return v ? labelOf(v) : undefined;
      }),
    [view, query, vars],
  );
  const chain = useMemo(
    () => (selected ? traceOf(view, selected) : null),
    [view, selected],
  );
  const lit: Trace | null =
    hover?.trace ??
    pinned?.trace ??
    chain ??
    (hits.length ? nodesTrace(hits.map((h) => h.component)) : null);

  const channels = Array.from({ length: view.channels }, (_, i) => i + 1);
  const grouped =
    view.channels > 1 && view.components.some((c) => c.channel != null);
  const toggle = (n: number) =>
    setExpanded((s) => {
      const next = new Set(s);
      if (next.has(n)) next.delete(n);
      else next.add(n);
      return next;
    });
  /** Select a component (or nothing), opening its channel when it is collapsed. */
  const select = (name: string | null) => {
    setSelected(name);
    setPinned(null);
    setHover(null);
    const ch = view.components.find((c) => c.name === name)?.channel;
    if (ch != null && !expanded.has(ch)) toggle(ch);
  };
  const focus: FocusHandlers = {
    pinned: pinned?.key ?? null,
    onHover: setHover,
    onPin: (f) => setPinned((p) => (f && p?.key === f.key ? null : f)),
    onSelect: select,
  };

  return (
    <div className="flex h-full min-h-0">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex flex-wrap items-center gap-3 border-b px-3 py-2">
          <div className="text-muted-foreground flex min-w-0 flex-1 basis-80 items-start gap-1.5 text-xs">
            <Info className="mt-0.5 size-3.5 shrink-0" />
            <span>{t("internals.hint")}</span>
          </div>
          <div className="relative w-60">
            <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2" />
            <Input
              type="search"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                if (selected) select(null);
              }}
              onKeyDown={(e) => {
                if (e.key === "Escape") setQuery("");
                if (e.key === "Enter" && hits.length) select(hits[0].component);
              }}
              placeholder={t("internals.find")}
              title={t("internals.findTip")}
              aria-label={t("internals.find")}
              className="num h-7 pl-7 text-xs"
              data-testid="internals-find"
            />
          </div>
          {grouped && (
            <div
              role="group"
              aria-label={t("internals.channels")}
              className="flex items-center gap-1"
            >
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
                onClick={() =>
                  setExpanded(
                    expanded.size === channels.length
                      ? new Set()
                      : new Set(channels),
                  )
                }
              >
                {expanded.size === channels.length
                  ? t("internals.collapseAll")
                  : t("internals.expandAll")}
              </Button>
            </div>
          )}
        </div>
        <div className="min-h-0 flex-1" data-testid="internals-graph">
          <ReactFlowProvider>
            <Graph
              view={view}
              vars={vars}
              expanded={expanded}
              selected={selected}
              lit={lit}
              onSelect={select}
              onExpand={toggle}
            />
          </ReactFlowProvider>
        </div>
      </div>
      <aside
        className="bg-sidebar w-80 shrink-0 border-l"
        aria-label={t("workbench.inspector")}
      >
        <ScrollArea className="h-full">
          {comp ? (
            <ComponentDetails comp={comp} inst={inst} view={view} focus={focus} />
          ) : query.trim() ? (
            <SearchResults view={view} query={query} hits={hits} onSelect={select} />
          ) : (
            <p className="text-muted-foreground p-4 text-xs">
              {t("internals.select")}
            </p>
          )}
        </ScrollArea>
      </aside>
    </div>
  );
}

/** How the details panel lights the graph: hover a row to see it, click to keep it lit. */
type FocusHandlers = {
  pinned: string | null;
  onHover: (f: Focus) => void;
  onPin: (f: Focus) => void;
  onSelect: (name: string) => void;
};

const NO_FOCUS: FocusHandlers = {
  pinned: null,
  onHover: () => {},
  onPin: () => {},
  onSelect: () => {},
};

/** The components a typed command or setting lands on (#739). */
function SearchResults({
  view,
  query,
  hits,
  onSelect,
}: {
  view: InstrumentInternals;
  query: string;
  hits: SearchHit[];
  onSelect: (name: string) => void;
}) {
  const { t } = useTranslation();
  if (!hits.length)
    return (
      <p className="text-muted-foreground p-4 text-xs" data-testid="internals-find-results">
        {t("internals.findNone", { query: query.trim() })}
      </p>
    );
  const byComp = new Map<string, SearchHit[]>();
  for (const h of hits) byComp.set(h.component, [...(byComp.get(h.component) ?? []), h]);
  return (
    <div className="space-y-1 p-3" data-testid="internals-find-results">
      <h3 className={HEADING}>{t("internals.findResults", { count: byComp.size })}</h3>
      <ul className="space-y-1">
        {[...byComp].map(([name, list]) => {
          const c = view.components.find((x) => x.name === name)!;
          return (
            <li key={name}>
              <button
                type="button"
                className="hover:bg-accent focus-visible:ring-ring w-full rounded-md px-2 py-1.5 text-left text-xs outline-none focus-visible:ring-2"
                onClick={() => onSelect(name)}
              >
                <div className="flex items-baseline justify-between gap-2">
                  <span className="font-medium">{typeLabel(t, c.type)}</span>
                  <span className="text-muted-foreground num">{name}</span>
                </div>
                {list.map((h) => (
                  <div
                    key={`${h.kind}:${h.text}`}
                    className="text-muted-foreground num text-[11px] break-all"
                  >
                    {h.text}
                  </div>
                ))}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

const nodeTypes = {
  component: memo(ComponentNode),
  group: memo(GroupNode),
  port: memo(PortNode),
  label: memo(LabelNode),
};

const HEADING =
  "text-muted-foreground mb-1.5 text-[11px] font-medium tracking-wide uppercase";

function Graph({
  view,
  vars,
  expanded,
  selected,
  lit,
  onSelect,
  onExpand,
}: {
  view: InstrumentInternals;
  vars: Map<string, StateVarInfo>;
  expanded: Set<number>;
  selected: string | null;
  lit: Trace | null;
  onSelect: (name: string | null) => void;
  onExpand: (channel: number) => void;
}) {
  const { t } = useTranslation();
  const theme = useTheme((s) => s.resolved);
  const graph = useMemo(() => buildGraph(view, expanded), [view, expanded]);
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
      type: n.kind,
      position: { x: n.x, y: n.y },
      data:
        n.kind === "component"
          ? { node: n, selected: n.comp.name === selected, lit: !dim && !!lit, dim }
          : { node: n, dim },
      draggable: false,
      connectable: false,
      selectable: n.kind !== "port",
    };
  });
  const base = { stroke: "var(--muted-foreground)", strokeWidth: 1.25 };
  const edges: Edge[] = graph.edges.map(({ kind, label, links, ...e }) => {
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
    return {
      ...e,
      type: "smoothstep",
      focusable: false,
      selectable: false,
      zIndex: on ? 1 : 0,
      // A signal wire is solid; what a component uses is dashed and named by its parameter (#729).
      ...(kind === "ref"
        ? {
            label,
            labelStyle: { fontSize: 10, fill: "var(--muted-foreground)" },
            labelBgStyle: { fill: "var(--background)" },
          }
        : {}),
      style,
      data: { kind, lit: on },
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
    <ReactFlow
      // A new layout (a channel expanded) fits the view again.
      key={[...expanded].sort().join(",")}
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      onNodeClick={(_, n) => {
        if (n.type === "component")
          onSelect((n.data as ComponentData).node.comp.name);
        else if (n.type === "group")
          onExpand((n.data as GroupData).node.channel);
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
      <Background
        variant={BackgroundVariant.Dots}
        gap={20}
        size={1.2}
        color="var(--border)"
      />
      <Controls showInteractive={false} position="bottom-left" />
    </ReactFlow>
  );
}

function typeLabel(t: T, type: string): string {
  return t(`internals.types.${type}`, { defaultValue: type });
}

function ComponentNode({ data }: NodeProps<Node<ComponentData>>) {
  const { t } = useTranslation();
  const { node, selected, lit, dim } = data;
  const c = node.comp;
  const index = (row: PortRow) =>
    node.rows.filter((r) => r.side === row.side).indexOf(row);
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
        <div className="truncate text-[13px] leading-tight font-medium">
          {typeLabel(t, c.type)}
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
              style={{
                top,
                width: 8,
                height: 8,
                borderWidth: 1.5,
                borderColor: "var(--muted-foreground)",
                background: "var(--card)",
              }}
            />
            <span
              className="num text-muted-foreground pointer-events-none absolute text-[10px] leading-none"
              style={{
                top,
                transform: "translateY(-50%)",
                ...(left ? { left: 10 } : { right: 10 }),
              }}
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

export function ComponentDetails({
  comp,
  inst,
  view,
  focus = NO_FOCUS,
}: {
  comp: InternalComponent;
  inst: InstrumentInfo;
  view: InstrumentInternals;
  focus?: FocusHandlers;
}) {
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
            <span
              className="num font-semibold"
              style={{ color: channelColor(comp.channel) }}
            >
              CH{comp.channel}
            </span>
          ) : (
            <span className="text-muted-foreground">
              {t("internals.whole")}
            </span>
          )}
        </div>
        {comp.doc && (
          <p className="text-muted-foreground mt-2 text-xs leading-relaxed">
            {comp.doc}
          </p>
        )}
      </div>
      <ControlSection comp={comp} view={view} vars={vars} focus={focus} />
      <Readings comp={comp} view={view} vars={vars} focus={focus} />
      <section>
        <h3 className={HEADING}>{t("internals.params")}</h3>
        {comp.params.length === 0 ? (
          <p className="text-muted-foreground text-xs">
            {t("internals.noParams")}
          </p>
        ) : (
          <dl className="space-y-1.5">
            {comp.params.map((p) => {
              const bound = p.binding ? vars.get(p.binding) : undefined;
              return (
                <div key={p.name} className="text-xs">
                  <div className="flex items-baseline justify-between gap-2">
                    <dt className="num text-muted-foreground truncate">
                      {p.name}
                    </dt>
                    <dd className="num truncate font-medium">
                      {formatParam(p.value, p.unit)}
                    </dd>
                  </div>
                  {p.binding && (
                    <div className="text-primary text-[11px]">
                      {t("internals.follows", {
                        label: bound
                          ? `${labelOf(bound)} (${p.binding})`
                          : p.binding,
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </dl>
        )}
      </section>
      <RefList comp={comp} view={view} />
      <section>
        <h3 className={HEADING}>{t("internals.ports")}</h3>
        <dl className="space-y-1">
          {comp.ports.map((p) => (
            <div
              key={p.name}
              className="flex items-baseline justify-between gap-2 text-xs"
            >
              <dt className="num">{p.name}</dt>
              <dd className="text-muted-foreground">
                {t(`internals.direction.${p.direction}`, {
                  defaultValue: p.direction,
                })}{" "}
                ·{" "}
                {t(`internals.medium.${p.medium}`, { defaultValue: p.medium })}
              </dd>
            </div>
          ))}
        </dl>
      </section>
    </div>
  );
}

/** A row that lights part of the graph while hovered and keeps it lit when clicked (#739). */
function FocusRow({
  id,
  trace,
  focus,
  label,
  children,
}: {
  id: string;
  trace: Trace;
  focus: FocusHandlers;
  label: string;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const pinned = focus.pinned === id;
  return (
    <li
      className={cn(
        "group/row relative rounded-md border border-transparent px-2 py-1.5 text-xs",
        "hover:bg-accent/60",
        pinned && "border-primary/50 bg-accent/60",
      )}
      onMouseEnter={() => focus.onHover({ key: id, trace })}
      onMouseLeave={() => focus.onHover(null)}
    >
      {children}
      <button
        type="button"
        aria-pressed={pinned}
        aria-label={`${t("internals.pin")}: ${label}`}
        title={t("internals.pin")}
        className={cn(
          "focus-visible:ring-ring absolute top-1.5 right-1.5 rounded p-0.5 outline-none focus-visible:ring-2",
          pinned
            ? "text-primary"
            : "text-muted-foreground opacity-0 group-hover/row:opacity-100 focus-visible:opacity-100",
        )}
        onFocus={() => focus.onHover({ key: id, trace })}
        onBlur={() => focus.onHover(null)}
        onClick={() => focus.onPin({ key: id, trace })}
      >
        <Pin className="size-3" />
      </button>
    </li>
  );
}

/** A component name that selects it. */
function CompLink({
  name,
  view,
  focus,
}: {
  name: string;
  view: InstrumentInternals;
  focus: FocusHandlers;
}) {
  const { t } = useTranslation();
  const c = view.components.find((x) => x.name === name);
  return (
    <button
      type="button"
      className="text-primary focus-visible:ring-ring rounded-sm underline-offset-2 outline-none hover:underline focus-visible:ring-2"
      title={name}
      onClick={() => focus.onSelect(name)}
    >
      {c ? typeLabel(t, c.type) : name}
      {c?.channel != null && (
        <span className="num ml-0.5 text-[10px]">CH{c.channel}</span>
      )}
    </button>
  );
}

/** What drives the component: each setting with its value and the command that writes it, then the commands that run its operations (#739). */
function ControlSection({
  comp,
  view,
  vars,
  focus,
}: {
  comp: InternalComponent;
  view: InstrumentInternals;
  vars: Map<string, StateVarInfo>;
  focus: FocusHandlers;
}) {
  const { t } = useTranslation();
  const rows = controlRows(view, comp);
  const ops = operationCommands(comp);
  return (
    <section data-testid="internal-controls">
      <h3 className={HEADING}>
        {rows.length || !ops.length
          ? t("internals.controls")
          : t("internals.commandsOnly")}
      </h3>
      {!rows.length && !ops.length && (
        <p className="text-muted-foreground text-xs">
          {t("internals.noControls")}
        </p>
      )}
      {rows.length > 0 && (
        <ul className="-mx-2 space-y-0.5">
          {rows.map((r) => {
            const v = vars.get(r.setting);
            const label = v ? labelOf(v) : r.setting;
            return (
              <FocusRow
                key={r.setting}
                id={`setting:${r.setting}`}
                trace={nodesTrace([comp.name, ...r.alsoDrives])}
                focus={focus}
                label={label}
              >
                <div className="flex items-baseline justify-between gap-2 pr-5">
                  <span className="min-w-0 truncate font-medium" title={r.setting}>
                    {label}
                  </span>
                  <span className="num shrink-0">{v ? formatValue(v) : "—"}</span>
                </div>
                <div className="text-muted-foreground num text-[11px] break-all">
                  {r.commands.length ? (
                    r.commands.join("  ")
                  ) : (
                    <span className="font-sans">{t("internals.noWriter")}</span>
                  )}
                </div>
                {r.alsoDrives.length > 0 && (
                  <div className="text-muted-foreground mt-0.5 text-[11px]">
                    {t("internals.alsoDrives")}{" "}
                    {r.alsoDrives.map((n, i) => (
                      <span key={n}>
                        {i > 0 && "、"}
                        <CompLink name={n} view={view} focus={focus} />
                      </span>
                    ))}
                  </div>
                )}
              </FocusRow>
            );
          })}
        </ul>
      )}
      {ops.length > 0 && (
        <div className={cn(rows.length > 0 && "mt-2")}>
          {rows.length > 0 && (
            <div className="text-muted-foreground mb-1 text-[11px]">
              {t("internals.operations")}
            </div>
          )}
          <ul className="num flex flex-wrap gap-x-3 gap-y-0.5 text-xs">
            {ops.map((h) => (
              <li key={h} className="break-all">
                {h}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

const SHOWN = 6;

/** The readings the component affects, one group per component that answers them, with the route (#739). */
function Readings({
  comp,
  view,
  vars,
  focus,
}: {
  comp: InternalComponent;
  view: InstrumentInternals;
  vars: Map<string, StateVarInfo>;
  focus: FocusHandlers;
}) {
  const { t } = useTranslation();
  const groups = readingGroups(comp);
  const main = groups.filter((g) => g.kind !== "onTheWay");
  const onTheWay = groups.filter((g) => g.kind === "onTheWay");
  const count = onTheWay.reduce((n, g) => n + g.headers.length, 0);
  const list = (gs: ReadingGroup[]) => (
    <ul className="-mx-2 space-y-0.5">
      {gs.map((g) => (
        <ReadingRow key={g.provider} g={g} comp={comp} view={view} vars={vars} focus={focus} />
      ))}
    </ul>
  );
  return (
    <section data-testid="internal-readings">
      <h3 className={HEADING}>{t("internals.readings")}</h3>
      {!groups.length && (
        <p className="text-muted-foreground text-xs">{t("internals.noReadings")}</p>
      )}
      {main.length > 0 && list(main)}
      {onTheWay.length > 0 && (
        <details className="group/way mt-1" open={!main.length}>
          <summary className="text-muted-foreground hover:text-foreground cursor-pointer text-[11px] select-none">
            {t("internals.onTheWay", { count })}
          </summary>
          <p className="text-muted-foreground my-1 text-[11px]">
            {t("internals.onTheWayHint")}
          </p>
          {list(onTheWay)}
        </details>
      )}
    </section>
  );
}

function ReadingRow({
  g,
  comp,
  view,
  vars,
  focus,
}: {
  g: ReadingGroup;
  comp: InternalComponent;
  view: InstrumentInternals;
  vars: Map<string, StateVarInfo>;
  focus: FocusHandlers;
}) {
  const { t } = useTranslation();
  const [all, setAll] = useState(false);
  const shown = all ? g.headers : g.headers.slice(0, SHOWN);
  const sel = g.selectedBy ? vars.get(g.selectedBy) : undefined;
  const picked = sel ? pickedChannel(sel.value) : null;
  const provider = view.components.find((c) => c.name === g.provider);
  return (
    <FocusRow
      id={`reading:${g.provider}`}
      trace={pathTrace(g.path)}
      focus={focus}
      label={provider ? typeLabel(t, provider.type) : g.provider}
    >
      <div className="flex flex-wrap items-center gap-x-1 pr-5 text-[11px]">
        {g.kind === "self" ? (
          <span className="font-medium">{t("internals.own")}</span>
        ) : (
          g.path.map((n, i) => (
            <span key={n} className="inline-flex items-center gap-1">
              {i > 0 && (
                <ArrowRight className="text-muted-foreground size-3" aria-hidden />
              )}
              {n === comp.name ? (
                <span className="text-muted-foreground">
                  {typeLabel(t, comp.type)}
                </span>
              ) : (
                <CompLink name={n} view={view} focus={focus} />
              )}
            </span>
          ))
        )}
      </div>
      {g.selectedBy && (
        <div className="text-muted-foreground text-[11px]">
          {picked === comp.channel
            ? t("internals.pickedHere", {
                label: sel ? labelOf(sel) : g.selectedBy,
              })
            : t("internals.pickedOther", {
                label: sel ? labelOf(sel) : g.selectedBy,
                value: sel ? formatValue(sel) : "—",
              })}
        </div>
      )}
      <ul className="num mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5 text-xs">
        {shown.map((h) => (
          <li key={h} className="break-all">
            {h}
          </li>
        ))}
        {g.headers.length > SHOWN && (
          <li>
            <button
              type="button"
              className="text-primary focus-visible:ring-ring rounded-sm text-[11px] outline-none hover:underline focus-visible:ring-2"
              onClick={() => setAll((a) => !a)}
            >
              {all
                ? t("internals.fewer")
                : t("internals.more", { count: g.headers.length - SHOWN })}
            </button>
          </li>
        )}
      </ul>
    </FocusRow>
  );
}

/** What the component uses (it takes their output) and who uses it (they take its output), #729. */
function RefList({
  comp,
  view,
}: {
  comp: InternalComponent;
  view: InstrumentInternals;
}) {
  const { t } = useTranslation();
  const refs = view.refs ?? [];
  const uses = refs.filter((r) => r.to === comp.name);
  const usedBy = refs.filter((r) => r.from === comp.name);
  const wired = view.wires.some(
    (w) =>
      w.from.startsWith(`${comp.name}.`) || w.to.startsWith(`${comp.name}.`),
  );
  const drivenByCommands =
    !uses.length &&
    !usedBy.length &&
    !wired &&
    !view.ports.some((p) => p.target.startsWith(`${comp.name}.`));
  if (!uses.length && !usedBy.length && !drivenByCommands) return null;
  const row = (r: (typeof refs)[number], other: string) => (
    <div
      key={`${r.from}>${r.to}:${r.param}`}
      className="flex items-baseline justify-between gap-2 text-xs"
    >
      <dt className="num truncate">{other}</dt>
      <dd className="text-muted-foreground num shrink-0">{r.param}</dd>
    </div>
  );
  return (
    <section data-testid="internal-refs">
      <h3 className="text-muted-foreground mb-1.5 text-[11px] font-medium tracking-wide uppercase">
        {t("internals.dependencies")}
      </h3>
      {drivenByCommands && (
        <p className="text-muted-foreground text-xs">
          {t("internals.commandSideHint")}
        </p>
      )}
      {uses.length > 0 && (
        <>
          <div className="text-muted-foreground mb-1 text-[11px]">
            {t("internals.uses")}
          </div>
          <dl className="mb-2 space-y-1">{uses.map((r) => row(r, r.from))}</dl>
        </>
      )}
      {usedBy.length > 0 && (
        <>
          <div className="text-muted-foreground mb-1 text-[11px]">
            {t("internals.usedBy")}
          </div>
          <dl className="space-y-1">{usedBy.map((r) => row(r, r.to))}</dl>
        </>
      )}
    </section>
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
