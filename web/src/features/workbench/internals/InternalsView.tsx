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
  ReactFlowProvider,
  getSmoothStepPath,
  useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/base.css";
import { Activity, ArrowRight, BookOpen, Crosshair, Pin, Search } from "lucide-react";
import { type ReactNode, memo, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useWorkbench } from "../store";
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
import { prefersReducedMotion } from "@/lib/motion";
import { useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";
import { formatValue, labelOf, numeric, stateMap } from "../model";
import { useInternalProbes } from "@/api/hooks";
import { InternalProbePanel, type Tuned } from "./ProbeView";
import { type MiniSignal, type Tune, CENTER, flowTag, miniOf, miniPath, probeable } from "./probe";
import {
  type GraphEdge,
  type GraphNode,
  NODE_WIDTH,
  REF_HANDLE_TOP,
  buildGraph,
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

/** Read-only view of how an instrument is built from components (issue 20). */
export function InternalsView({
  session,
  inst,
}: {
  session: SessionInfo;
  inst: InstrumentInfo;
}) {
  const version = useDeferredValue(useEvents((s) => s.version));
  const revealSetting = useWorkbench((s) => s.revealSetting);
  const query = useInternals(session.id, inst.name, version);
  if (query.error)
    return (
      <ErrorState error={query.error} onRetry={() => void query.refetch()} />
    );
  if (!query.data) return <Skeleton className="m-3 h-[calc(100%-1.5rem)]" />;
  return <LiveInternals session={session} inst={inst} view={query.data} onLocate={(path) => revealSetting(inst.name, path)} />;
}

/** A value once it has stopped changing for `ms`: a burst of events asks for the thumbnails once. */
function useSettled<V>(value: V, ms: number): V {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const id = window.setTimeout(() => setSettled(value), ms);
    return () => window.clearTimeout(id);
  }, [value, ms]);
  return settled;
}

/**
 * The internals of a running session: the diagram, the signal at every output on screen (the
 * thumbnails), and the probe panel. The thumbnails follow the event version (D64) once a burst of
 * events has settled; when the service withholds internal signals (validation mode) they are not
 * asked for again and the diagram shows its structure alone.
 */
function LiveInternals({
  session,
  inst,
  view,
  onLocate,
}: {
  session: SessionInfo;
  inst: InstrumentInfo;
  view: InstrumentInternals;
  onLocate: (setting: string) => void;
}) {
  const version = useSettled(useEvents((s) => s.version), 200);
  const [ports, setPorts] = useState<string[]>([]);
  const [withheld, setWithheld] = useState(false);
  const read = useInternalProbes(session.id, inst.name, ports, version, !withheld);
  // Refused once is refused for the session's mode: remembered, so the next version does not ask again.
  if (read.withheld && !withheld) setWithheld(true);
  const signals = useMemo(() => new Map([...read.probes].map(([port, probe]) => [port, miniOf(probe)])), [read.probes]);
  return (
    <InternalsPanel
      view={view}
      inst={inst}
      onLocate={onLocate}
      signals={signals}
      onPorts={setPorts}
      renderProbe={(probe) => (
        <InternalProbePanel
          key={probe.port}
          sid={session.id}
          instrument={inst.name}
          port={probe.port}
          heading={probe.heading}
          tag={probe.tag}
          tuned={probe.tuned}
          onClose={probe.close}
        />
      )}
    />
  );
}

/** What the panel hands the probe it shows: the port, its title and its wire's label, and how to close it. */
export type ProbeSlot = {
  port: string;
  heading: string;
  tag: string;
  /** A receiver's tuning context and where the probe is tuned (#959); absent for other instruments. */
  tuned?: Tuned;
  close: () => void;
};

/** What lights up on the graph: a hovered or pinned row, else the selection's chain, else search hits. */
type Focus = { key: string; trace: Trace } | null;

/** The graph and the details of the selected component, from data already loaded. */
export function InternalsPanel({
  view,
  inst,
  initial,
  onLocate,
  renderProbe,
  signals,
  onPorts,
}: {
  view: InstrumentInternals;
  inst: InstrumentInfo;
  /** The signal at each output port on screen, drawn on its block. Without it the blocks show their ports alone. */
  signals?: ReadonlyMap<string, MiniSignal>;
  /** Told which output ports are on screen (the expanded channels' and the whole instrument's), so their signals can be read. */
  onPorts?: (ports: string[]) => void;
  /** Where the view starts (stories): a selected component, a search, or an open probe. */
  initial?: { selected?: string; query?: string; probe?: string; tune?: Tune };
  /** Show a setting in the workbench inspector (#800). The acceptance page has no inspector and leaves it out. */
  onLocate?: (setting: string) => void;
  /** The internal probe on an output port (#953). Without it (the acceptance page: no session) ports are not probed. */
  renderProbe?: (probe: ProbeSlot) => ReactNode;
}) {
  const { t } = useTranslation();
  const [selected, setSelected] = useState<string | null>(
    initial?.selected ?? (initial?.probe ? componentOf(initial.probe) : null),
  );
  // CH1 open, and the channel of a component the view starts on.
  const [expanded, setExpanded] = useState<Set<number>>(() => {
    const ch = view.components.find((c) => c.name === selected)?.channel;
    return new Set(ch != null ? [1, ch] : [1]);
  });
  const [probe, setProbe] = useState<string | null>(initial?.probe ?? null);
  // Where the probe is tuned (a receiver, #959). Kept while the probe moves from port to port: tune
  // once, then follow that frequency down the chain — mixer, IF, ADC, down-converter, transform.
  const [tune, setTune] = useState<Tune>(initial?.tune ?? CENTER);
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
    if (name !== selected) setProbe(null);
    const ch = view.components.find((c) => c.name === name)?.channel;
    if (ch != null && !expanded.has(ch)) toggle(ch);
  };
  // The outputs on screen: every probed output of a block that is drawn (not inside a collapsed channel).
  const shownPorts = useMemo(
    () =>
      view.components
        .filter((c) => c.channel == null || expanded.has(c.channel))
        .flatMap((c) => c.ports.filter(probeable).map((p) => `${c.name}.${p.name}`)),
    [view, expanded],
  );
  const shownKey = shownPorts.join("|");
  useEffect(() => {
    onPorts?.(shownKey ? shownKey.split("|") : []);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the port list, by value
  }, [shownKey]);
  /** Open the probe on an output: its component is selected too, so its details follow below. */
  const openProbe = (port: string) => {
    select(componentOf(port));
    setProbe(port);
  };
  const probeSlot: ProbeSlot | null =
    probe && renderProbe
      ? {
          port: probe,
          ...probeTitle(t, view, probe),
          tuned: view.tuning ? { tuning: view.tuning, tune, onTune: setTune } : undefined,
          close: () => setProbe(null),
        }
      : null;
  const focus: FocusHandlers = {
    pinned: pinned?.key ?? null,
    onHover: setHover,
    onPin: (f) => setPinned((p) => (f && p?.key === f.key ? null : f)),
    onSelect: select,
  };

  return (
    <div className="flex h-full min-h-0">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="space-y-1.5 border-b px-3 py-2">
         <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <div className="relative w-56">
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
          <Legend probes={!!renderProbe} />
         </div>
         <Basis view={view} />
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
              onProbe={renderProbe ? openProbe : undefined}
              probing={probe}
              signals={signals}
            />
          </ReactFlowProvider>
        </div>
      </div>
      {/* The side column is there when it has something to say (a block, a probe, search results):
          until then the diagram has the whole width, and the legend says what a click does. */}
      {(comp || probeSlot || query.trim()) && (
        <aside className="bg-sidebar w-80 shrink-0 border-l" aria-label={t("workbench.inspector")}>
          <ScrollArea className="h-full">
            {probeSlot && renderProbe?.(probeSlot)}
            {comp ? (
              <ComponentDetails comp={comp} inst={inst} view={view} focus={focus} onLocate={onLocate} />
            ) : (
              <SearchResults view={view} query={query} hits={hits} onSelect={select} />
            )}
          </ScrollArea>
        </aside>
      )}
    </div>
  );
}

/** The component of a '<component>.<port>' reference (a component name may itself hold dots: 'ch1.front'). */
function componentOf(port: string): string {
  return port.slice(0, port.lastIndexOf("."));
}

/** A probe's title — the block in the engineer's words and the port (`ADC 组 · out1`) — and its wire's label. */
function probeTitle(t: T, view: InstrumentInternals, port: string): { heading: string; tag: string } {
  const name = componentOf(port);
  const c = view.components.find((x) => x.name === name);
  const channel = c?.channel != null ? ` CH${c.channel}` : "";
  const pin = port.slice(name.length + 1);
  // A port the engineer has a name for (`混频器 · 中频输出`); else the port as the model names it.
  const heading = `${c ? typeLabel(t, c.type) : name}${channel} · ${c ? t(`internals.portNames.${c.type}.${pin}`, { defaultValue: pin }) : pin}`;
  const wire = view.wires.find((w) => w.from === port);
  const medium = c?.ports.find((p) => p.name === port.slice(name.length + 1))?.medium;
  const tag = flowTag(t, wire ?? { medium: medium === "samples" ? "samples" : "signal", sample_rate_hz: null, format: null });
  return { heading, tag };
}

/**
 * The page's fixed statement (signal-chain.md 1.2): a block diagram drawn from public documents,
 * not the model's circuit; and the basis it was drawn from — the definition's ``reference`` (D42).
 */
function Basis({ view }: { view: InstrumentInternals }) {
  const { t } = useTranslation();
  const ref = view.reference;
  const basis = ref ? [ref.title, ref.document, ref.version].filter(Boolean).join(" · ") : null;
  const whole = `${t("internals.basis")} ${basis ? `${t("internals.basisLabel")} ${basis}` : t("internals.basisNone")}`;
  return (
    <p className="text-muted-foreground flex items-center gap-1.5 text-[11px]" data-testid="internals-basis" title={whole}>
      <BookOpen className="size-3.5 shrink-0" aria-hidden />
      <span className="truncate">
        {t("internals.basis")}{" "}
        {basis ? (
          <>
            <span className="text-foreground font-medium">{t("internals.basisLabel")}</span>{" "}
            <span className="text-foreground">{basis}</span>
          </>
        ) : (
          t("internals.basisNone")
        )}
      </span>
    </p>
  );
}

/**
 * How to read the diagram, drawn rather than described: each kind of line as it appears on the
 * graph next to its name (signal-chain.md 1.2 — shape and words, not colour). The full sentence
 * stays as the legend's description.
 */
function Legend({ probes }: { probes: boolean }) {
  const { t } = useTranslation();
  const line = "var(--muted-foreground)";
  return (
    <ul
      className="text-muted-foreground ml-auto flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px]"
      title={t("internals.hint")}
      aria-label={t("internals.hint")}
      data-testid="internals-legend"
    >
      <li className="flex items-center gap-1.5">
        <svg width="22" height="8" aria-hidden>
          <path d="M0,4H22" stroke={line} strokeWidth="1.25" />
        </svg>
        {t("internals.flow.analog")}
      </li>
      <li className="flex items-center gap-1.5">
        <svg width="22" height="8" aria-hidden>
          <path d="M0,4H22" stroke={line} strokeWidth="4.5" />
          <path d="M0,4H22" stroke="var(--background)" strokeWidth="1.75" />
        </svg>
        {t("internals.legend.digital")}
      </li>
      <li className="flex items-center gap-1.5">
        <svg width="22" height="8" aria-hidden>
          <path d="M0,4H22" stroke={line} strokeWidth="1.25" strokeDasharray="4 3" />
        </svg>
        {t("internals.legend.uses")}
      </li>
      {probes && (
        <li className="flex items-center gap-1">
          <Activity className="size-3" aria-hidden />
          {t("internals.legend.probe")}
        </li>
      )}
    </ul>
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
  onProbe,
  probing,
  signals,
}: {
  signals?: ReadonlyMap<string, MiniSignal>;
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
  const [overflows, setOverflows] = useState(false);
  // The view opens readable, not merely complete: the whole graph when it fits at a size its text
  // can be read at, otherwise at that size from the left — where the signal comes in — and the
  // overview map says where the rest is.
  const home = () => {
    const box = wrap.current?.getBoundingClientRect();
    if (!box?.width || !box.height) return null;
    const b = boundsOf(graph.nodes);
    const fit = Math.min((box.width - 2 * VIEW_PAD) / b.width, (box.height - 2 * VIEW_PAD) / b.height);
    const zoom = Math.min(MAX_OPEN_ZOOM, Math.max(fit, READABLE_ZOOM));
    const along = (size: number, from: number, extent: number) =>
      extent * zoom <= size - 2 * VIEW_PAD ? (size - extent * zoom) / 2 - from * zoom : VIEW_PAD - from * zoom;
    return { x: along(box.width, b.x, b.width), y: along(box.height, b.y, b.height), zoom };
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
      type: n.kind,
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
    <div
      ref={wrap}
      className="h-full w-full"
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
        const opened = home();
        if (!opened) return;
        void flow.setViewport(opened);
        setOverflows(outside(opened));
        if (selected) reveal(selected, false);
      }}
      onMoveEnd={(_, v) => setOverflows(outside(v))}
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
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
      {overflows && (
        <MiniMap
          pannable
          zoomable
          position="bottom-right"
          ariaLabel={t("internals.overview")}
          style={{ width: 168, height: 92 }}
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
      )}
    </ReactFlow>
    </div>
  );
}

/** The smallest size the view opens at: below it the blocks' names and port labels cannot be read. */
const READABLE_ZOOM = 0.72;
const MAX_OPEN_ZOOM = 1.15;
const VIEW_PAD = 24;

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

function typeLabel(t: T, type: string): string {
  return t(`internals.types.${type}`, { defaultValue: type });
}

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
                  "nodrag nopan num focus-visible:ring-ring group/port absolute flex items-center justify-end gap-1.5 rounded-md py-0.5 pr-1 pl-0.5 text-[10px] leading-none outline-none focus-visible:ring-2",
                  probing === port
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:bg-accent hover:text-foreground",
                )}
                style={{ top, left: MINI.left - 2, right: 6, transform: "translateY(-50%)" }}
                aria-label={t("internals.probe.open", { port: `${typeLabel(t, c.type)} · ${row.name}` })}
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
                className="num text-muted-foreground pointer-events-none absolute text-[10px] leading-none"
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

export function ComponentDetails({
  comp,
  inst,
  view,
  focus = NO_FOCUS,
  onLocate,
}: {
  comp: InternalComponent;
  inst: InstrumentInfo;
  view: InstrumentInternals;
  focus?: FocusHandlers;
  onLocate?: (setting: string) => void;
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
      <ControlSection comp={comp} view={view} vars={vars} focus={focus} onLocate={onLocate} />
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
  onLocate,
}: {
  comp: InternalComponent;
  view: InstrumentInternals;
  vars: Map<string, StateVarInfo>;
  focus: FocusHandlers;
  onLocate?: (setting: string) => void;
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
                  {onLocate && v && (
                    <button
                      type="button"
                      aria-label={`${t("internals.locate")}: ${label}`}
                      title={t("internals.locate")}
                      data-testid={`internal-locate-${r.setting}`}
                      className="text-muted-foreground hover:text-foreground focus-visible:ring-ring shrink-0 rounded p-0.5 outline-none focus-visible:ring-2"
                      onClick={() => onLocate(r.setting)}
                    >
                      <Crosshair className="size-3" />
                    </button>
                  )}
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
