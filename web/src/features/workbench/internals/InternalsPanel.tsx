/**
 * The internals panel (issue 20, #739) — the orchestrator the workbench's live view and the
 * acceptance page share: the toolbar, the graph (./Graph) and the details of the selected
 * component, with the state both pages listen to (selection, channels, search, the probe, full
 * screen, the overview map).
 */
import { ReactFlowProvider } from "@xyflow/react";
import { Activity, ArrowRight, BookOpen, Crosshair, Map as MapIcon, Maximize2, Minimize2, Pin, Search } from "lucide-react";
import { type ReactNode, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { T } from "@/i18n";
import type {
  InstrumentInfo,
  InstrumentInternals,
  InternalComponent,
  StateVarInfo,
} from "@/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { TypeDescription } from "@/components/app/TypeTitle";
import { blockLabel, internalType } from "@/lib/component";
import { channelColor } from "@/lib/channels";
import { displayUnit, formatEng } from "@/lib/format";
import { load, save } from "@/lib/storage";
import { cn } from "@/lib/utils";
import { formatValue, labelOf, numeric } from "../model";
import { stateMap } from "@/api/stateMap";
import { type Tuned } from "./ProbeView";
import { type MiniSignal, type Tune, CENTER, flowTag, probeable } from "./probe";
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
import { Graph } from "./Graph";

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
  // Full screen is in-app (#1174): the same tree, restyled to cover the viewport, so the viewport,
  // selection and probe survive the switch and a future editor reuses one structure for both forms.
  const [fullscreen, setFullscreen] = useState(false);
  const [overview, setOverview] = useState(() => load<boolean>("internals-overview", false));
  const [overflows, setOverflows] = useState(false);
  useEffect(() => {
    if (!fullscreen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !e.defaultPrevented) setFullscreen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fullscreen]);
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
  // The channel just opened: the new layout brings its row into view (#1252).
  const [opened, setOpened] = useState<number | null>(null);
  const toggle = (n: number) => {
    setOpened(expanded.has(n) ? null : n);
    setExpanded((s) => {
      const next = new Set(s);
      if (next.has(n)) next.delete(n);
      else next.add(n);
      return next;
    });
  };
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
    <div
      // fixed inset-0 must not sit under a transformed or contained ancestor; the full-screen e2e
      // checks the viewport corners with elementFromPoint, which fails first if one appears (#1186).
      className={cn("flex min-h-0 flex-col", fullscreen ? "bg-background fixed inset-0 z-50" : "h-full")}
      data-testid="internals-panel"
      data-fullscreen={fullscreen}
    >
        {/* The toolbar spans the side column too: opening it (a block chosen) leaves the toolbar's
            width, its wrapping, and so the diagram's top edge where they were (#1252). */}
        <div className="space-y-1.5 border-b px-3 py-2" data-testid="internals-toolbar">
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
                if (e.key === "Escape" && query) {
                  setQuery("");
                  e.preventDefault();
                }
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
                onClick={() => {
                  setOpened(null);
                  setExpanded(
                    expanded.size === channels.length
                      ? new Set()
                      : new Set(channels),
                  );
                }}
              >
                {expanded.size === channels.length
                  ? t("internals.collapseAll")
                  : t("internals.expandAll")}
              </Button>
            </div>
          )}
          <Legend probes={!!renderProbe} />
          {/* The overview map covers a corner of the diagram: shown when asked for, and only while
              part of the graph is out of view (#1234). */}
          {overflows && (
            <Button
              size="sm"
              variant={overview ? "secondary" : "ghost"}
              className="ml-auto h-7 px-2"
              onClick={() => {
                save("internals-overview", !overview);
                setOverview(!overview);
              }}
              aria-pressed={overview}
              title={t("internals.overview")}
              aria-label={t("internals.overview")}
              data-testid="internals-overview"
            >
              <MapIcon className="size-3.5" />
            </Button>
          )}
          <Button
            size="sm"
            variant={fullscreen ? "secondary" : "ghost"}
            className={cn("h-7 gap-1 px-2 text-xs", !overflows && "ml-auto")}
            onClick={() => setFullscreen((f) => !f)}
            title={fullscreen ? t("internals.exitFullscreenTip") : t("internals.fullscreen")}
            aria-label={fullscreen ? t("internals.exitFullscreen") : t("internals.fullscreen")}
            data-testid="internals-fullscreen"
          >
            {fullscreen ? <Minimize2 className="size-3.5" /> : <Maximize2 className="size-3.5" />}
            {fullscreen && t("internals.exitFullscreen")}
          </Button>
         </div>
         <Basis view={view} />
        </div>
      <div className="flex min-h-0 flex-1">
        <div className="min-h-0 min-w-0 flex-1" data-testid="internals-graph">
          <ReactFlowProvider>
            <Graph
              view={view}
              vars={vars}
              expanded={expanded}
              selected={selected}
              lit={lit}
              onSelect={select}
              onExpand={toggle}
              opened={opened}
              onProbe={renderProbe ? openProbe : undefined}
              probing={probe}
              signals={signals}
              overview={overview && overflows}
              onOverflows={setOverflows}
            />
          </ReactFlowProvider>
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
  const heading = `${c ? blockLabel(c) : name}${channel} · ${c ? t(`internals.portNames.${c.type}.${pin}`, { defaultValue: pin }) : pin}`;
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
    <p className="text-muted-foreground flex items-start gap-1.5 text-[11px]" data-testid="internals-basis" title={whole}>
      <BookOpen className="mt-px size-3.5 shrink-0" aria-hidden />
      {/* Wraps when narrow: the statement is read whole, not cut off (#1234). */}
      <span>
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
                  <span className="font-medium">{blockLabel(c)}</span>
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

const HEADING =
  "text-muted-foreground mb-1.5 text-[11px] font-medium tracking-wide uppercase";

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
        <div className="text-sm font-semibold">{blockLabel(comp)}</div>
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
        {comp.description && (
          <p className="mt-2 text-xs leading-relaxed" data-testid="internal-description">
            {comp.description}
          </p>
        )}
        <TypeDescription of={internalType(comp)} doc={comp.doc} className="mt-2 text-xs" />
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
  const c = view.components.find((x) => x.name === name);
  return (
    <button
      type="button"
      className="text-primary focus-visible:ring-ring rounded-sm underline-offset-2 outline-none hover:underline focus-visible:ring-2"
      title={name}
      onClick={() => focus.onSelect(name)}
    >
      {c ? blockLabel(c) : name}
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
      label={provider ? blockLabel(provider) : g.provider}
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
                  {blockLabel(comp)}
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
