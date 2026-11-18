import {
  Background,
  BackgroundVariant,
  type Connection,
  Controls,
  type Edge,
  Handle,
  type Node,
  type NodeChange,
  type NodeProps,
  Position,
  ReactFlow,
  ReactFlowProvider,
  applyNodeChanges,
  useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { AlertTriangle, Check, GripVertical, Layers, Maximize2, Minimize2, RotateCcw, Shuffle, SlidersHorizontal, Unlink, LayoutGrid, Link2, Loader2, Plus, Redo2, Search, Terminal, Trash2, Undo2 } from "lucide-react";
import { type DragEvent, type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import {
  useComponents,
  useDefinitionLayout,
  useDefinitionStructure,
  useSaveDefinitionLayout,
  useSaveDefinitionStructure,
} from "@/api/hooks";
import { useEvents } from "@/api/events";
import { DraftInput, INVALID, commit } from "@/components/app/DraftInput";
import { ErrorState } from "@/components/app/StateViews";
import { Button } from "@/components/ui/button";
import { IdentityPanel } from "./IdentityPanel";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";
import { typeAbout, typeName } from "@/lib/component";
import { TypeDescription, TypeTitle } from "@/components/app/TypeTitle";
import {
  EMPTY_HISTORY,
  type History,
  componentName,
  recordEdit,
  redo,
  renameComponent,
  renameInLayout,
  undo,
  type ComponentDoc,
  type End,
  type Point,
  type PortRow,
  type Scope,
  type Structure,
  type StructureComponent,
  addComponent,
  BAND_GAP,
  FOLLOW_ROW,
  FOLLOW_ROWS,
  NODE_HEAD as HEAD,
  NODE_WIDTH,
  PORT_ROW,
  ROW,
  blockHeight,
  panelPortsOf,
  autoLayout,
  canWire,
  channelBand,
  connect,
  diagnosticNode,
  followedSettings,
  followers,
  refLinks,
  inBand,
  isTable,
  libraryGroups,
  portSignature,
  removeTableRow,
  setTableRow,
  tableKey,
  literalText,
  nodeId,
  panelPortName,
  paramKind,
  paramNames,
  parseLiteral,
  parseNodeId,
  parseNumber,
  portsOf,
  removalCounts,
  removeComponent,
  removePanelPort,
  removeWire,
  setPanelPort,
  setParam,
  settingChoices,
  structureOf,
  wiresOf,
} from "./model";
import { Hint, Tip } from "./glyphs";
import { CommandsPanel, SettingsPanel } from "./SettingsPanel";

type DefinitionStructure = NonNullable<ReturnType<typeof useDefinitionStructure>["data"]>;

/** What the library hands the canvas on a drag: a component type. */
export const BUILD_MIME = "application/x-simubench-component";
const BAND_ID = "band";
/** The sides a reference line can leave and enter a node by, picked from where the two are. */
const SIDES = [
  ["top", Position.Top],
  ["bottom", Position.Bottom],
  ["left", Position.Left],
  ["right", Position.Right],
] as const;

/** A reference line runs side to side between neighbours in a row, else bottom to top. */
function refSides(from: Point | undefined, to: Point | undefined): [string, string] {
  if (!from || !to) return ["bottom", "top"];
  if (Math.abs(to.y - from.y) < ROW * 0.6) return to.x >= from.x ? ["right", "left"] : ["left", "right"];
  return to.y > from.y ? ["bottom", "top"] : ["top", "bottom"];
}
/** The smallest zoom the canvas opens at: node text stays about 11px or more. */
const MIN_READABLE = 0.85;

// ---- the positions, beside the definition in the workspace (#1207) ---------------------------

/** Where the components are drawn: `instruments/<id>.layout.json` for a workspace definition (no
 * version, no session restart); a built-in model keeps them on this page only. */
function useLayout(definitionId: string, editable: boolean) {
  const { t } = useTranslation();
  const query = useDefinitionLayout(definitionId);
  const save = useSaveDefinitionLayout(definitionId);
  const [positions, setPositions] = useState<Record<string, Point> | null>(null);
  if (positions === null && query.data) setPositions(query.data.positions);
  const update = useCallback(
    (next: Record<string, Point>) => {
      setPositions(next);
      if (editable)
        save.mutate({ positions: next }, { onError: (e: Error) => toast.error(t("acceptance.build.layoutFailed", { message: e.message })) });
    },
    [editable, save, t],
  );
  return { positions: positions ?? {}, ready: !query.isLoading, update };
}

// ---- saving: one structure at a time, the newest wins -----------------------------------------

/**
 * The structure the canvas shows and its saves: an edit shows at once and is sent whole; while a
 * save is out the next edit waits and goes with the hash that save returns, so two quick edits
 * never race each other into a 409. A failed save puts the canvas back to what the server holds.
 */
function useBuildState(definitionId: string, server: DefinitionStructure | undefined, reload: () => void) {
  const { t } = useTranslation();
  const save = useSaveDefinitionStructure(definitionId);
  const [draft, setDraft] = useState<Structure | null>(null);
  const [busy, setBusy] = useState(false);
  const running = useRef(false);
  const queued = useRef<Structure | null>(null);
  const sha = useRef<string | null>(null);
  const [seen, setSeen] = useState<string | null>(null);
  const [history, setHistory] = useState<History>(EMPTY_HISTORY);
  // A new file from elsewhere replaces the draft when nothing waits (our own saves set `seen`), and
  // the steps to undo no longer apply to it.
  if (server && server.sha256 !== seen && !busy) {
    setSeen(server.sha256);
    setDraft(structureOf(server));
    setHistory(EMPTY_HISTORY);
  }
  useEffect(() => {
    if (server && !running.current) sha.current = server.sha256;
  }, [server]);

  const flush = useCallback(async () => {
    running.current = true;
    setBusy(true);
    try {
      while (queued.current) {
        const next = queued.current;
        queued.current = null;
        const saved = await save.mutateAsync({ base_sha256: sha.current ?? "", ...next });
        sha.current = saved.sha256;
        if (!queued.current) {
          setSeen(saved.sha256);
          setDraft(structureOf(saved));
        }
      }
    } catch (e) {
      queued.current = null;
      setSeen(null); // take the server's structure again
      reload();
      if (e instanceof ApiError && e.status === 409) toast.warning(t("acceptance.build.conflict"));
      else toast.error(t("acceptance.build.saveFailed", { message: (e as Error).message }));
    } finally {
      running.current = false;
      setBusy(false);
    }
  }, [save, t, reload]);

  const send = useCallback(
    (next: Structure) => {
      setDraft(next);
      queued.current = next;
      if (!running.current) void flush();
    },
    [flush],
  );
  const apply = useCallback(
    (next: Structure | null) => {
      if (!next || !draft) return false;
      setHistory((h) => recordEdit(h, draft));
      send(next);
      return true;
    },
    [draft, send],
  );
  // An undo is saved as a new version like any edit: the history of the file only grows (D35).
  const step = useCallback(
    (move: typeof undo) => {
      if (!draft) return;
      const done = move(history, draft);
      if (!done) return;
      setHistory(done.history);
      send(done.structure);
    },
    [draft, history, send],
  );
  return {
    draft,
    apply,
    busy,
    undo: () => step(undo),
    redo: () => step(redo),
    canUndo: history.past.length > 0,
    canRedo: history.future.length > 0,
  };
}

// ---- the page ----------------------------------------------------------------------------------

/** The build area of a definition (#1201, instrument-authoring.md 9): its structure on a canvas
 * the user edits by dragging. Read-only for a built-in model and while disconnected. */
export function BuildTab({
  definitionId,
  tryPanel,
  instrumentClass = null,
}: {
  definitionId: string;
  tryPanel?: ReactNode;
  /** The definition's class: its own component types head the library (P2-4). */
  instrumentClass?: string | null;
}) {
  const { t } = useTranslation();
  const structure = useDefinitionStructure(definitionId);
  const components = useComponents();
  const status = useEvents((s) => s.status);
  const { refetch } = structure;
  const reload = useCallback(() => void refetch(), [refetch]);
  const build = useBuildState(definitionId, structure.data, reload);
  const { draft, apply, busy } = build;
  const docs = useMemo(() => new Map((components.data ?? []).map((c) => [c.type, c])), [components.data]);
  const [selected, setSelected] = useState<string | null>(null);
  const [trying, setTrying] = useState(false);
  // A setting the side panel points at (hovered or open): the canvas lights the components that
  // follow it (#1212).
  const [focus, setFocus] = useState<string | null>(null);
  // A component type being dragged from the library: the per-channel drop zone shows meanwhile.
  const [dragging, setDragging] = useState(false);
  // The side panel opened wide for the long lists of settings and commands (#1214).
  const [wide, setWide] = useState(false);
  const editable = !!structure.data?.editable && status !== "reconnecting" && status !== "gone";
  const layout = useLayout(definitionId, !!structure.data?.editable);
  const { undo: undoStep, redo: redoStep } = build;

  // Ctrl/⌘+Z and Ctrl/⌘+Shift+Z (or Ctrl+Y) undo and redo, except while typing in a field, whose
  // own undo the browser keeps.
  useEffect(() => {
    if (!editable) return;
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
      const el = e.target as HTMLElement | null;
      if (el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName))) return;
      const key = e.key.toLowerCase();
      if (key === "z" && !e.shiftKey) undoStep();
      else if ((key === "z" && e.shiftKey) || key === "y") redoStep();
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [editable, undoStep, redoStep]);

  if (structure.isLoading || components.isLoading || !layout.ready) {
    return (
      <div className="relative flex h-full gap-3 p-3" aria-busy="true">
        <Skeleton className="h-full w-56" />
        <Skeleton className="h-full flex-1" />
        <p className="text-muted-foreground absolute inset-0 flex items-center justify-center gap-1.5 text-xs" role="status">
          <Loader2 className="size-3.5 animate-spin motion-reduce:animate-none" /> {t("acceptance.build.loading")}
        </p>
      </div>
    );
  }
  if (structure.error || components.error || !structure.data || !draft) {
    return (
      <ErrorState
        error={structure.error ?? components.error}
        onRetry={() => {
          void structure.refetch();
          void components.refetch();
        }}
      />
    );
  }
  const data = structure.data;
  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="build-area">
      <Toolbar
        data={data}
        busy={busy}
        draft={draft}
        editable={editable}
        onRelayout={() => layout.update({})}
        onUndo={build.canUndo ? build.undo : undefined}
        onRedo={build.canRedo ? build.redo : undefined}
        trying={trying}
        onTry={tryPanel ? () => setTrying((v) => !v) : undefined}
      />
      <div className="flex min-h-0 flex-1">
        {editable && <Library docs={components.data ?? []} onDrag={setDragging} instrumentClass={instrumentClass} />}
        <div className="relative min-w-0 flex-1">
          <ReactFlowProvider>
            <BuildCanvas
              definitionId={definitionId}
              saved={layout.positions}
              onLayout={layout.update}
              structure={draft}
              channels={data.channels}
              docs={docs}
              editable={editable}
              diagnostics={data.diagnostics}
              onChange={apply}
              selected={selected}
              onSelect={setSelected}
              focus={focus}
              dragging={dragging}
            />
          </ReactFlowProvider>
        </div>
        <aside
          className={cn("bg-sidebar shrink-0 overflow-y-auto border-l transition-[width] motion-reduce:transition-none", wide ? "w-[44rem] max-w-[60%]" : "w-72")}
          aria-label={t("acceptance.build.params")}
          data-testid="build-side"
          data-wide={wide || undefined}
        >
          <SidePanel
            structure={draft}
            docs={docs}
            editable={editable}
            selected={selected}
            onSelect={setSelected}
            onChange={apply}
            onFocus={setFocus}
            definitionId={definitionId}
            wide={wide}
            onWide={setWide}
            onRename={(from, to) => {
              if (layout.positions[from]) layout.update(renameInLayout(layout.positions, from, to));
              setSelected(to);
            }}
          />
        </aside>
      </div>
      {trying && tryPanel && (
        <section className="flex h-72 shrink-0 flex-col border-t" aria-label={t("acceptance.build.try")} data-testid="build-try">
          <div className="text-muted-foreground border-b px-3 py-1 text-[11px]">{t("acceptance.build.tryHint")}</div>
          <div className="min-h-0 flex-1">{tryPanel}</div>
        </section>
      )}
    </div>
  );
}

function Toolbar({
  data,
  busy,
  draft,
  editable,
  onRelayout,
  onUndo,
  onRedo,
  trying,
  onTry,
}: {
  data: DefinitionStructure;
  busy: boolean;
  draft: Structure;
  editable: boolean;
  onRelayout: () => void;
  onUndo?: () => void;
  onRedo?: () => void;
  trying: boolean;
  onTry?: () => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const problems = data.diagnostics.filter((d) => d.severity === "error");
  const inherited = data.inherited.map((k) => t(`acceptance.build.item.${k}`)).join("、");
  return (
    <div className="border-b">
      {!data.editable && (
        <div className="bg-sim-muted text-sim px-4 py-1.5 text-[11px]" role="status">
          {t("acceptance.build.readOnly")}
        </div>
      )}
      {data.editable && data.extends && data.inherited.length > 0 && (
        <div className="text-muted-foreground border-b px-4 py-1.5 text-[11px]" role="note">
          {t("acceptance.build.inherited", { parent: data.extends, items: inherited })}
        </div>
      )}
      <div className="flex items-center gap-3 px-3 py-1.5 text-[11px]">
        {data.editable && (
          <span className="text-muted-foreground inline-flex items-center gap-1" role="status" aria-live="polite">
            {busy ? <Loader2 className="size-3.5 animate-spin motion-reduce:animate-none" /> : <Check className="size-3.5" />}
            {busy ? t("acceptance.build.saving") : t("acceptance.build.saved")}
          </span>
        )}
        <button
          type="button"
          className={cn("inline-flex items-center gap-1 rounded px-1", problems.length ? "text-error" : "text-muted-foreground")}
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          disabled={!data.diagnostics.length}
          data-testid="build-problems"
        >
          {problems.length ? <AlertTriangle className="size-3.5" /> : <Check className="size-3.5" />}
          {problems.length ? t("acceptance.build.problems", { count: problems.length }) : t("acceptance.build.noProblems")}
        </button>
        {editable && (
          <div className="ml-auto flex items-center">
            <Button
              variant="ghost"
              size="icon"
              className="size-7"
              onClick={onUndo}
              disabled={!onUndo}
              title={t("acceptance.build.undo")}
              aria-label={t("acceptance.build.undo")}
            >
              <Undo2 className="size-3.5" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="size-7"
              onClick={onRedo}
              disabled={!onRedo}
              title={t("acceptance.build.redo")}
              aria-label={t("acceptance.build.redo")}
            >
              <Redo2 className="size-3.5" />
            </Button>
          </div>
        )}
        <Tip label={t("acceptance.build.relayoutHint")}>
          <Button
            variant="ghost"
            size="icon"
            className={cn("size-7", !editable && "ml-auto")}
            aria-label={t("acceptance.build.relayout")}
            onClick={onRelayout}
            disabled={!draft.components.length && !draft.channel_components.length}
          >
            <LayoutGrid className="size-3.5" />
          </Button>
        </Tip>
        {onTry && (
          <Button variant={trying ? "secondary" : "outline"} size="sm" className="h-7" onClick={onTry} aria-pressed={trying} data-testid="build-try-toggle">
            <Terminal className="size-3.5" /> {t("acceptance.build.try")}
          </Button>
        )}
      </div>
      {open && data.diagnostics.length > 0 && (
        <ul className="max-h-32 space-y-0.5 overflow-y-auto border-t px-4 py-1.5 text-[11px]" role="list">
          {data.diagnostics.map((d, i) => (
            <li key={`${d.code}-${i}`} className={d.severity === "error" ? "text-error" : "text-muted-foreground"}>
              {d.code}: {d.message}
              {d.pointer && <span className="num ml-1 opacity-70">{d.pointer}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Library({
  docs,
  onDrag,
  instrumentClass,
}: {
  docs: ComponentDoc[];
  onDrag: (dragging: boolean) => void;
  instrumentClass: string | null;
}) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const [all, setAll] = useState(false);
  const { groups, hidden } = libraryGroups(docs, query, instrumentClass, all);
  const className = instrumentClass ? t(`acceptance.class.${instrumentClass}`, { defaultValue: instrumentClass }) : "";
  return (
    <aside className="bg-sidebar flex w-60 shrink-0 flex-col border-r" aria-label={t("acceptance.build.library")}>
      <div className="space-y-1.5 border-b p-2">
        <div className="flex items-center justify-between">
          <h2 className="text-xs font-medium">{t("acceptance.build.library")}</h2>
          <Hint text={t("acceptance.build.dragHint")} />
        </div>
        <div className="relative">
          <Search className="text-muted-foreground absolute top-1/2 left-2 size-3.5 -translate-y-1/2" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("acceptance.build.filter")}
            aria-label={t("acceptance.build.filter")}
            className="h-7 pl-7 text-xs"
          />
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto pb-2" data-testid="build-library">
        {groups.map((g) => {
          const title = g.group === "own" ? t("acceptance.build.groups.own", { class: className }) : t(`acceptance.build.groups.${g.group}`);
          return (
            <section key={g.group} aria-label={title} data-testid={`build-library-group-${g.group}`}>
              <h3 className="bg-sidebar text-muted-foreground sticky top-0 z-[1] px-3 pt-2 pb-1 text-[11px] font-medium">
                {title} <span className="num opacity-70">{g.items.length}</span>
              </h3>
              <ul className="space-y-1 px-2">
                {g.items.map((d) => (
                  <LibraryItem key={d.type} doc={d} onDrag={onDrag} />
                ))}
              </ul>
            </section>
          );
        })}
        {instrumentClass && !query && (hidden > 0 || all) && (
          <Button
            variant="ghost"
            size="sm"
            className="text-muted-foreground mx-2 mt-2 h-7 w-[calc(100%-1rem)] justify-start text-[11px]"
            onClick={() => setAll(!all)}
            data-testid="build-library-all"
          >
            {all ? t("acceptance.build.ownOnly", { class: className }) : t("acceptance.build.showAll", { count: hidden })}
          </Button>
        )}
        {!groups.length && <p className="text-muted-foreground px-3 py-2 text-[11px]">{t("acceptance.build.noMatch")}</p>}
      </div>
    </aside>
  );
}

/** One component type in the library: its name with the type as a grey tag, a sentence of what it
 * does, and its ports (#1214, #1251). */
function LibraryItem({ doc: d, onDrag }: { doc: ComponentDoc; onDrag: (dragging: boolean) => void }) {
  const about = typeAbout(d) ?? "";
  const ports = portSignature(d);
  return (
    <li
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData(BUILD_MIME, d.type);
        e.dataTransfer.effectAllowed = "copy";
        onDrag(true);
      }}
      onDragEnd={() => onDrag(false)}
      title={about}
      data-testid={`build-library-${d.type}`}
      className="bg-card hover:border-primary/50 flex cursor-grab items-start gap-1.5 rounded-md border px-2 py-1.5 active:cursor-grabbing"
    >
      <GripVertical className="text-muted-foreground/50 mt-0.5 size-3.5 shrink-0" />
      <div className="min-w-0 flex-1 space-y-0.5">
        <TypeTitle of={d} className="text-[12.5px] leading-tight" />
        {about && <p className="text-muted-foreground truncate text-[11px] leading-snug">{about}</p>}
        {ports && <p className="text-muted-foreground/80 num truncate text-[10.5px]">{ports}</p>}
      </div>
    </li>
  );
}

// ---- the canvas --------------------------------------------------------------------------------

interface CompData extends Record<string, unknown> {
  comp: StructureComponent;
  scope: Scope;
  rows: PortRow[];
  panel: string[];
  problem: boolean;
  editable: boolean;
  /** The type's name (#1251): the block's heading, its own name under it. */
  title: string;
  /** The settings it follows (#1212), and whether the one the side panel points at is among them. */
  follows: { setting: string; params: string[] }[];
  lit: boolean;
  dim: boolean;
}

interface BandData extends Record<string, unknown> {
  w: number;
  h: number;
  label: string;
}

function CompNode({ data, selected }: NodeProps<Node<CompData>>) {
  const { t } = useTranslation();
  const { comp, scope, rows, panel, problem, editable, follows, lit, dim } = data;
  const left = rows.filter((r) => r.direction !== "out");
  const right = rows.filter((r) => r.direction === "out");
  const portRows = Math.max(left.length, right.length, 1);
  const shown = follows.slice(0, FOLLOW_ROWS);
  const height = blockHeight(rows, panel.length, follows.length);
  return (
    <div
      className={cn(
        "bg-card text-card-foreground relative rounded-lg border shadow-sm",
        selected && "ring-primary ring-2 shadow-md",
        lit && !selected && "ring-sim ring-2 shadow-md",
        dim && "opacity-45",
        problem && "border-error",
      )}
      style={{ width: NODE_WIDTH, height }}
      data-testid="build-component"
      data-name={comp.name}
      data-lit={lit || undefined}
      data-dim={dim || undefined}
    >
      {scope === "channel" && <div className="bg-primary/60 absolute inset-y-0 left-0 w-1 rounded-l-lg" aria-hidden />}
      <div className="flex items-start gap-1 border-b px-3 py-1.5" style={{ height: HEAD }}>
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13px] leading-tight font-medium">{data.title}</div>
          <div className="text-muted-foreground num truncate text-[11px] leading-tight">{comp.name}</div>
        </div>
        {problem && <AlertTriangle className="text-error size-3.5 shrink-0" aria-label={t("acceptance.build.problems", { count: 1 })} />}
      </div>
      {left.map((r, i) => (
        <PortLabel key={`l-${r.name}`} row={r} top={HEAD + i * PORT_ROW} side="left" editable={editable} />
      ))}
      {right.map((r, i) => (
        <PortLabel key={`r-${r.name}`} row={r} top={HEAD + i * PORT_ROW} side="right" editable={editable} />
      ))}
      {panel.length > 0 && (
        <div
          className="text-muted-foreground num absolute inset-x-0 truncate px-3 text-[10.5px]"
          style={{ top: HEAD + Math.max(left.length, right.length, 1) * PORT_ROW }}
        >
          ⊡ {panel.join(", ")}
        </div>
      )}
      {follows.length > 0 && (
        <ul
          className="absolute inset-x-0 space-y-0 border-t border-dashed px-3 pt-1"
          style={{ top: HEAD + portRows * PORT_ROW + (panel.length ? PORT_ROW : 0) }}
          data-testid="build-follows"
        >
          {shown.map((f) => (
            <li
              key={f.setting}
              className="text-sim num flex items-center gap-1 truncate text-[11px]"
              style={{ height: FOLLOW_ROW }}
              title={t("acceptance.build.followsTitle", { setting: f.setting, params: f.params.join(", ") })}
            >
              <SlidersHorizontal className="size-3 shrink-0" aria-hidden />
              <span className="truncate">{f.setting}</span>
            </li>
          ))}
          {follows.length > FOLLOW_ROWS && (
            <li className="text-muted-foreground text-[11px]" style={{ height: FOLLOW_ROW }}>
              {t("acceptance.build.followsMore", { count: follows.length - FOLLOW_ROWS })}
            </li>
          )}
        </ul>
      )}
      {/* where a reference line starts and ends (#1212); not for wiring by hand */}
      {SIDES.map(([side, position]) => (
        <span key={side}>
          <Handle id={`ref-s-${side}`} type="source" position={position} isConnectable={false} className="!pointer-events-none !opacity-0" />
          <Handle id={`ref-t-${side}`} type="target" position={position} isConnectable={false} className="!pointer-events-none !opacity-0" />
        </span>
      ))}
    </div>
  );
}

function PortLabel({ row, top, side, editable }: { row: PortRow; top: number; side: "left" | "right"; editable: boolean }) {
  const { t } = useTranslation();
  const pin = row.medium === "electrical";
  return (
    <div
      className={cn(
        "num absolute flex items-center text-[11px]",
        side === "left" ? "left-0 pl-3" : "right-0 justify-end pr-3",
        row.extra ? "text-muted-foreground/70 italic" : "text-muted-foreground",
      )}
      style={{ top, height: PORT_ROW, width: NODE_WIDTH / 2 }}
      title={pin ? t("acceptance.build.pin") : `${row.name} · ${row.medium}`}
    >
      {row.extra && <Plus className="mr-0.5 size-3" />}
      {row.name}
      {!pin && (
        <Handle
          id={row.name}
          type={side === "left" ? "target" : "source"}
          position={side === "left" ? Position.Left : Position.Right}
          isConnectable={editable}
          style={{ top: PORT_ROW / 2, width: 9, height: 9 }}
        />
      )}
    </div>
  );
}

function BandNode({ data }: NodeProps<Node<BandData>>) {
  return (
    <div
      className="border-primary/40 bg-primary/5 rounded-xl border border-dashed"
      style={{ width: data.w, height: data.h }}
      data-testid="build-band"
    >
      <div className="text-primary px-3 py-1 text-[11px] font-medium">{data.label}</div>
    </div>
  );
}

const nodeTypes = { comp: CompNode, band: BandNode };

function BuildCanvas({
  definitionId,
  saved,
  onLayout,
  structure,
  channels,
  docs,
  editable,
  diagnostics,
  onChange,
  selected,
  onSelect,
  focus,
  dragging,
}: {
  definitionId: string;
  saved: Record<string, Point>;
  onLayout: (next: Record<string, Point>) => void;
  structure: Structure;
  channels: number;
  docs: ReadonlyMap<string, ComponentDoc>;
  editable: boolean;
  diagnostics: DefinitionStructure["diagnostics"];
  onChange: (next: Structure | null) => boolean;
  selected: string | null;
  onSelect: (id: string | null) => void;
  focus: string | null;
  dragging: boolean;
}) {
  const { t } = useTranslation();
  const flow = useReactFlow();
  const theme = useTheme((s) => s.resolved);
  const positions = useMemo(() => autoLayout(structure, saved, docs), [structure, saved, docs]);
  const band = useMemo(() => channelBand(positions, structure, NODE_WIDTH), [positions, structure]);
  const problems = useMemo(
    () => new Set(diagnostics.filter((d) => d.severity === "error").map((d) => diagnosticNode(structure, d.pointer))),
    [diagnostics, structure],
  );
  const rowsOf = useCallback(
    (scope: Scope, c: StructureComponent) => portsOf(structure, scope, c.name, docs.get(c.type)),
    [structure, docs],
  );

  const lit = useMemo(() => (focus ? new Set(followers(structure, focus)) : null), [focus, structure]);
  const hasBand = structure.channel_components.length > 0;

  const built = useMemo<Node[]>(() => {
    const panelOf = (scope: Scope, name: string) => panelPortsOf(structure, scope, name);
    const comp = (scope: Scope) => (c: StructureComponent): Node<CompData> => {
      const id = nodeId(scope, c.name);
      return {
        id,
        type: "comp",
        position: positions[id],
        selected: selected === id,
        data: {
          comp: c,
          scope,
          rows: rowsOf(scope, c),
          panel: panelOf(scope, c.name),
          problem: problems.has(id),
          editable,
          title: typeName(docs.get(c.type) ?? { type: c.type }),
          follows: followedSettings(c),
          lit: !!lit?.has(id),
          dim: !!lit && lit.size > 0 && !lit.has(id),
        },
      };
    };
    // The per-channel band holds the channel components; an instrument without any shows none (a
    // drop zone appears while a component is dragged, #1212).
    const bandNode: Node<BandData>[] = hasBand
      ? [
      {
        id: BAND_ID,
        type: "band",
        position: { x: band.x, y: band.y },
        draggable: false,
        selectable: false,
        deletable: false,
        zIndex: -1,
        data: {
          w: band.w,
          h: band.h,
          label: t("acceptance.build.band", { count: channels }),
        },
      },
        ]
      : [];
    return [...bandNode, ...structure.channel_components.map(comp("channel")), ...structure.components.map(comp("instrument"))];
  }, [structure, positions, band, rowsOf, problems, editable, selected, channels, t, lit, hasBand, docs]);

  const [nodes, setNodes] = useState<Node[]>(built);
  const [prevBuilt, setPrevBuilt] = useState(built);
  if (prevBuilt !== built) {
    setPrevBuilt(built);
    setNodes((ns) => {
      const old = new Map(ns.map((n) => [n.id, n]));
      return built.map((n) => {
        const o = old.get(n.id);
        return o ? { ...o, data: n.data, selected: n.selected, position: o.dragging ? o.position : n.position } : n;
      });
    });
  }

  const edges = useMemo<Edge[]>(
    () => [
      ...wiresOf(structure).map((w): Edge => {
        const id = `w:${w.list}:${w.index}`;
        return {
          id,
          source: nodeId(w.from.scope, w.from.name),
          sourceHandle: w.from.port,
          target: nodeId(w.to.scope, w.to.name),
          targetHandle: w.to.port,
          selected: selected === id,
          deletable: editable,
          style: { strokeWidth: selected === id ? 2.5 : 1.5, stroke: selected === id ? "var(--primary)" : "var(--muted-foreground)" },
        };
      }),
      // A reference (`store: cal`) as a dashed line with the parameter's name (#1212): shown, not
      // edited here — the parameter in the side panel changes it.
      ...refLinks(structure, docs).map((r): Edge => {
        const [out, into] = refSides(positions[r.from], positions[r.to]);
        return {
          id: `ref|${r.from}|${r.param}`,
          source: r.from,
          sourceHandle: `ref-s-${out}`,
          target: r.to,
          targetHandle: `ref-t-${into}`,
          selectable: false,
          deletable: false,
          focusable: false,
          label: r.param,
          labelStyle: { fontSize: 10, fill: "var(--muted-foreground)" },
          labelBgStyle: { fill: "var(--background)" },
          style: { strokeWidth: 1.2, stroke: "var(--muted-foreground)", strokeDasharray: "4 3", opacity: 0.8 },
          className: "build-ref",
        };
      }),
    ],
    [structure, selected, editable, docs, positions],
  );

  const rowFor = (id: string | null, handle: string | null | undefined): PortRow | undefined => {
    const at = id ? parseNodeId(id) : null;
    if (!at || !handle) return undefined;
    const c = (at.scope === "instrument" ? structure.components : structure.channel_components).find((x) => x.name === at.name);
    return c ? rowsOf(at.scope, c).find((r) => r.name === handle) : undefined;
  };

  const onNodesChange = (changes: NodeChange[]) => {
    setNodes((ns) => applyNodeChanges(changes.filter((c) => c.type !== "remove"), ns));
  };

  const onConnect = (c: Connection) => {
    const from = parseNodeId(c.source);
    const to = parseNodeId(c.target);
    if (!from || !to || !c.sourceHandle || !c.targetHandle) return;
    const a: End = { ...from, port: c.sourceHandle };
    const b: End = { ...to, port: c.targetHandle };
    if (!onChange(connect(structure, a, b))) toast.warning(t("acceptance.build.wireRefused"));
  };

  const remove = (nodeIds: string[], removed: Edge[]) => {
    let next = structure;
    // A deleted component's own wires go with it (and are counted there); the other selected
    // wires first, highest index first so the others keep their place.
    const gone = new Set(nodeIds);
    const wires = removed
      .filter((e) => !gone.has(e.source) && !gone.has(e.target))
      .map((e) => e.id.split(":"))
      .filter((p) => p.length === 3)
      .map(([, list, index]) => ({ list: list as Scope, index: Number(index) }))
      .sort((x, y) => y.index - x.index);
    for (const w of wires) next = removeWire(next, w.list, w.index);
    for (const id of nodeIds) {
      const at = parseNodeId(id);
      if (!at) continue;
      const counts = removalCounts(next, at.scope, at.name);
      next = removeComponent(next, at.scope, at.name, docs);
      toast.success(t("acceptance.build.removed", { name: at.name, ...counts }));
    }
    if (next !== structure) {
      onChange(next);
      if (wires.length && !nodeIds.length) toast.success(t("acceptance.build.wireRemoved"));
      onSelect(null);
    }
  };

  const onDragOver = (e: DragEvent) => {
    if (editable && e.dataTransfer.types.includes(BUILD_MIME)) {
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
    }
  };
  const onDrop = (e: DragEvent) => {
    const type = e.dataTransfer.getData(BUILD_MIME);
    if (!editable || !type) return;
    e.preventDefault();
    const at = flow.screenToFlowPosition({ x: e.clientX, y: e.clientY });
    const scope: Scope = hasBand && inBand(at, band) ? "channel" : "instrument";
    const { structure: next, name } = addComponent(structure, type, scope);
    const id = nodeId(scope, name);
    const layout = { ...saved, [id]: { x: Math.round(at.x - NODE_WIDTH / 2), y: Math.round(at.y - HEAD / 2) } };
    onLayout(layout);
    onChange(next);
    onSelect(id);
    toast.success(t("acceptance.build.added", { name }));
  };

  useEffect(() => {
    // Readable at open, not complete (as the internals view, ui-design.md): text stays legible;
    // the fit-view control shows the whole when wanted.
    const id = window.setTimeout(async () => {
      await flow.fitView({ padding: 0.08, minZoom: MIN_READABLE, maxZoom: 1, duration: 0 });
      const { zoom } = flow.getViewport();
      if (zoom <= MIN_READABLE) {
        // too big to fit readably: start at the top left, where the channel band is
        const b = flow.getNodesBounds(flow.getNodes());
        flow.setViewport({ x: 24 - b.x * MIN_READABLE, y: 24 - b.y * MIN_READABLE, zoom: MIN_READABLE });
      }
    }, 0);
    return () => window.clearTimeout(id);
  }, [flow, definitionId]);

  // With no channel components yet, a strip on top of the canvas takes the first one while a
  // component is dragged; it lands above everything, where the band will then be drawn.
  const onDropChannel = (e: DragEvent) => {
    const type = e.dataTransfer.getData(BUILD_MIME);
    if (!editable || !type) return;
    e.preventDefault();
    e.stopPropagation();
    const { structure: next, name } = addComponent(structure, type, "channel");
    const id = nodeId("channel", name);
    const pts = Object.values(positions);
    const top = pts.length ? Math.min(...pts.map((p) => p.y)) : 0;
    const left = pts.length ? Math.min(...pts.map((p) => p.x)) : 0;
    onLayout({ ...saved, [id]: { x: left, y: top - ROW - BAND_GAP } });
    onChange(next);
    onSelect(id);
    toast.success(t("acceptance.build.added", { name }));
  };

  const empty = !structure.components.length && !structure.channel_components.length;
  return (
    <>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onNodeDragStop={(_, n) => {
          if (n.id === BAND_ID) return;
          const layout = { ...saved, [n.id]: { x: Math.round(n.position.x), y: Math.round(n.position.y) } };
          onLayout(layout);
        }}
        onNodeClick={(_, n) => onSelect(n.id === BAND_ID ? null : n.id)}
        onEdgeClick={(_, e) => e.id.startsWith("w:") && onSelect(e.id)}
        onPaneClick={() => onSelect(null)}
        onConnect={onConnect}
        isValidConnection={(c) => canWire(rowFor(c.source, c.sourceHandle), rowFor(c.target, c.targetHandle))}
        onBeforeDelete={async ({ nodes: ns, edges: es }) => {
          remove(
            ns.map((n) => n.id).filter((id) => id !== BAND_ID),
            es,
          );
          return false;
        }}
        onDragOver={onDragOver}
        onDrop={onDrop}
        nodesDraggable
        nodesConnectable={editable}
        elementsSelectable
        deleteKeyCode={editable ? ["Delete", "Backspace"] : null}
        connectionLineStyle={{ stroke: "var(--primary)", strokeWidth: 2 }}
        zoomOnDoubleClick={false}
        colorMode={theme}
        minZoom={0.2}
        maxZoom={2}
        proOptions={{ hideAttribution: true }}
        aria-label={t("acceptance.section.build")}
      >
        <Background variant={BackgroundVariant.Dots} gap={20} size={1.2} color="var(--border)" />
        <Controls showInteractive={false} position="bottom-left" />
      </ReactFlow>
      {editable && dragging && !hasBand && (
        <div
          className="border-primary/50 bg-primary/10 text-primary absolute inset-x-4 top-3 z-10 flex h-16 items-center justify-center rounded-xl border border-dashed text-xs font-medium"
          onDragOver={onDragOver}
          onDrop={onDropChannel}
          data-testid="build-band-drop"
        >
          {t("acceptance.build.bandEmpty")}
        </div>
      )}
      {empty && (
        <p className="text-muted-foreground pointer-events-none absolute inset-x-0 bottom-16 text-center text-xs">
          {editable ? t("acceptance.build.empty") : ""}
        </p>
      )}
    </>
  );
}

// ---- the side panel ----------------------------------------------------------------------------

function SidePanel({
  structure,
  docs,
  editable,
  selected,
  onSelect,
  onChange,
  onRename,
  onFocus,
  definitionId,
  wide,
  onWide,
}: {
  structure: Structure;
  docs: ReadonlyMap<string, ComponentDoc>;
  editable: boolean;
  selected: string | null;
  onSelect: (id: string | null) => void;
  onChange: (next: Structure | null) => boolean;
  onRename: (fromId: string, toId: string) => void;
  onFocus: (setting: string | null) => void;
  definitionId: string;
  wide: boolean;
  onWide: (wide: boolean) => void;
}) {
  const { t } = useTranslation();
  const at = selected ? parseNodeId(selected) : null;
  const comp = at
    ? (at.scope === "instrument" ? structure.components : structure.channel_components).find((c) => c.name === at.name)
    : undefined;
  if (at && comp) {
    return (
      <ComponentPanel
        key={selected}
        structure={structure}
        scope={at.scope}
        comp={comp}
        docs={docs}
        editable={editable}
        onChange={onChange}
        onSelect={onSelect}
        onRename={onRename}
      />
    );
  }
  const wire = selected?.startsWith("w:") ? selected.split(":") : null;
  if (wire) {
    const list = wire[1] as Scope;
    const index = Number(wire[2]);
    const pair = structure.wiring[list]?.[index];
    if (pair) {
      return (
        <div className="space-y-3 p-3 text-xs">
          <h2 className="font-medium">{t("acceptance.build.wire")}</h2>
          <p className="num break-all">
            {pair[0]} → {pair[1]}
          </p>
          {editable && (
            <Button
              variant="outline"
              size="sm"
              className="h-7"
              onClick={() => {
                onChange(removeWire(structure, list, index));
                onSelect(null);
              }}
            >
              <Trash2 className="size-3.5" /> {t("acceptance.build.deleteWire")}
            </Button>
          )}
        </div>
      );
    }
  }
  return (
    <Overview
      structure={structure}
      docs={docs}
      editable={editable}
      onChange={onChange}
      onFocus={onFocus}
      definitionId={definitionId}
      wide={wide}
      onWide={onWide}
    />
  );
}

/** With nothing selected: the panel ports, the settings and the commands (#1206). */
function Overview({
  structure,
  docs,
  editable,
  onChange,
  onFocus,
  definitionId,
  wide,
  onWide,
}: {
  structure: Structure;
  docs: ReadonlyMap<string, ComponentDoc>;
  editable: boolean;
  onChange: (next: Structure | null) => boolean;
  onFocus: (setting: string | null) => void;
  definitionId: string;
  wide: boolean;
  onWide: (wide: boolean) => void;
}) {
  const { t } = useTranslation();
  // Leaving the settings page lets go of the setting it pointed at.
  useEffect(() => () => onFocus(null), [onFocus]);
  return (
    <Tabs defaultValue="ports" className="gap-0" onValueChange={(v) => v !== "settings" && onFocus(null)}>
      <div className="flex items-center border-b px-2">
        <TabsList variant="line" className="h-9">
          <TabsTrigger value="ports" className="text-xs" data-testid="build-tab-ports">
            {t("acceptance.build.tabPorts")}
          </TabsTrigger>
          <TabsTrigger value="settings" className="text-xs" data-testid="build-tab-settings">
            {t("acceptance.build.tabSettings")}
          </TabsTrigger>
          <TabsTrigger value="commands" className="text-xs" data-testid="build-tab-commands">
            {t("acceptance.build.tabCommands")}
          </TabsTrigger>
          <TabsTrigger value="identity" className="text-xs" data-testid="build-tab-identity">
            {t("acceptance.build.tabIdentity")}
          </TabsTrigger>
        </TabsList>
        <Button
          variant="ghost"
          size="icon"
          className="ml-auto size-7"
          onClick={() => onWide(!wide)}
          aria-pressed={wide}
          title={wide ? t("acceptance.build.narrow") : t("acceptance.build.wide")}
          aria-label={wide ? t("acceptance.build.narrow") : t("acceptance.build.wide")}
          data-testid="build-wide"
        >
          {wide ? <Minimize2 className="size-3.5" /> : <Maximize2 className="size-3.5" />}
        </Button>
      </div>
      <TabsContent value="ports">
        <PanelPorts structure={structure} docs={docs} editable={editable} onChange={onChange} />
      </TabsContent>
      <TabsContent value="settings">
        <SettingsPanel structure={structure} editable={editable} onChange={onChange} onFocus={onFocus} wide={wide} />
      </TabsContent>
      <TabsContent value="commands">
        <CommandsPanel structure={structure} docs={docs} editable={editable} onChange={onChange} definitionId={definitionId} wide={wide} />
      </TabsContent>
      <TabsContent value="identity">
        <IdentityPanel definitionId={definitionId} editable={editable} />
      </TabsContent>
    </Tabs>
  );
}

function ComponentPanel({
  structure,
  scope,
  comp,
  docs,
  editable,
  onChange,
  onSelect,
  onRename,
}: {
  structure: Structure;
  scope: Scope;
  comp: StructureComponent;
  docs: ReadonlyMap<string, ComponentDoc>;
  editable: boolean;
  onChange: (next: Structure | null) => boolean;
  onSelect: (id: string | null) => void;
  onRename: (fromId: string, toId: string) => void;
}) {
  const { t } = useTranslation();
  const doc = docs.get(comp.type);
  const keys = paramNames(comp, doc);
  const counts = removalCounts(structure, scope, comp.name);
  return (
    <div className="space-y-3 p-3 text-xs" data-testid="build-component-panel">
      <div>
        <div className="text-muted-foreground text-[11px]">{scope === "channel" ? t("acceptance.build.channel") : t("acceptance.build.instrument")}</div>
        <h2 className="text-sm font-medium">{doc ? typeName(doc) : comp.type}</h2>
        <div className="num text-muted-foreground">{comp.type}</div>
        <label className="mt-1.5 block space-y-0.5">
          <span className="text-muted-foreground text-[10.5px]">{t("acceptance.build.name")}</span>
          <DraftInput<string>
            text={comp.name}
            disabled={!editable}
            className="num h-7 text-xs"
            aria-label={t("acceptance.build.name")}
            parse={(typed) => {
              const n = componentName(structure, typed, comp.name);
              return n ? commit(n) : INVALID;
            }}
            onInvalid={() => toast.error(t("acceptance.build.nameInvalid"))}
            onCommit={(n) => {
              if (n === comp.name) return;
              if (onChange(renameComponent(structure, scope, comp.name, n, docs))) onRename(nodeId(scope, comp.name), nodeId(scope, n));
            }}
          />
        </label>
        {doc && <TypeDescription of={doc} doc={doc.doc} className="mt-1.5" />}
      </div>
      <section className="space-y-1.5">
        <h3 className="font-medium">{t("acceptance.build.params")}</h3>
        {!keys.length && <p className="text-muted-foreground">{t("acceptance.build.noParams")}</p>}
        {keys.map((key) => (
          <ParamRow key={key} structure={structure} scope={scope} comp={comp} doc={doc} name={key} editable={editable} onChange={onChange} />
        ))}
      </section>
      {editable && (
        <Button
          variant="outline"
          size="sm"
          className="text-error h-7"
          onClick={() => {
            onChange(removeComponent(structure, scope, comp.name, docs));
            onSelect(null);
            toast.success(t("acceptance.build.removed", { name: comp.name, ...counts }));
          }}
        >
          <Trash2 className="size-3.5" /> {t("acceptance.build.delete")}
        </Button>
      )}
    </div>
  );
}

const NONE = "__none__";

function ParamRow({
  structure,
  scope,
  comp,
  doc,
  name,
  editable,
  onChange,
}: {
  structure: Structure;
  scope: Scope;
  comp: StructureComponent;
  doc: ComponentDoc | undefined;
  name: string;
  editable: boolean;
  onChange: (next: Structure | null) => boolean;
}) {
  const { t } = useTranslation();
  const kind = paramKind(comp, doc, name);
  const info = doc?.params.find((p) => p.name === name);
  const choices = settingChoices(structure, scope);
  const id = `param-${comp.name}-${name}`;
  return (
    <div className="space-y-0.5" title={info?.doc ?? undefined}>
      <label htmlFor={id} className="num flex items-baseline gap-1 text-[11px]">
        <span className="font-medium">{name}</span>
        {info?.unit && <span className="text-muted-foreground">{info.unit}</span>}
      </label>
      {kind.kind === "setting" && (
        // One look for a parameter that follows a setting, bound (`$x`, can be unbound here) or
        // through a mapping (written by the agent) (#1214).
        <div className="bg-sim-muted flex h-7 items-center gap-1.5 rounded-md px-2" data-testid={`binding-${comp.name}-${name}`}>
          <SlidersHorizontal className="text-sim size-3.5 shrink-0" aria-hidden />
          <span
            className="text-sim num min-w-0 flex-1 truncate text-[11px]"
            title={t("acceptance.build.followsSetting", { setting: kind.setting })}
            aria-label={t("acceptance.build.followsSetting", { setting: kind.setting })}
          >
            {kind.setting}
          </span>
          {typeof comp.params?.[name] === "string"
            ? editable && (
                <Tip label={t("acceptance.build.unbind")}>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-5"
                    aria-label={t("acceptance.build.unbind")}
                    onClick={() => onChange(setParam(structure, scope, comp.name, name, undefined))}
                  >
                    <Unlink className="size-3" />
                  </Button>
                </Tip>
              )
            : (
                <Tip label={t("acceptance.build.mappedHint")}>
                  <span role="img" aria-label={t("acceptance.build.mapped")} className="text-muted-foreground inline-flex shrink-0" data-mapped>
                    <Shuffle className="size-3" />
                  </span>
                </Tip>
              )}
        </div>
      )}
      {kind.kind === "ref" && (
        <Select
          value={kind.value ?? NONE}
          disabled={!editable}
          onValueChange={(v) => onChange(setParam(structure, scope, comp.name, name, v === NONE ? undefined : v))}
        >
          <SelectTrigger id={id} className="num h-7 w-full text-xs" size="sm">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={NONE}>{t("acceptance.build.refNone")}</SelectItem>
            {(kind.scope === "instrument" ? structure.components : structure.channel_components)
              .filter((c) => c.name !== comp.name && (!kind.target || c.type === kind.target))
              .map((c) => (
                <SelectItem key={c.name} value={c.name} className="num">
                  {c.name}
                </SelectItem>
              ))}
            {kind.target &&
              (kind.scope === "instrument" ? structure.components : structure.channel_components)
                .filter((c) => c.name !== comp.name && c.type !== kind.target)
                .map((c) => (
                  <SelectItem key={c.name} value={c.name} className="num text-muted-foreground">
                    {c.name}
                  </SelectItem>
                ))}
          </SelectContent>
        </Select>
      )}
      {kind.kind === "literal" && isTable(kind.value) && (
        <TableEditor
          id={id}
          table={kind.value}
          editable={editable}
          isDefault={!kind.set}
          onChange={(next) => onChange(setParam(structure, scope, comp.name, name, next))}
          onReset={kind.set ? () => onChange(setParam(structure, scope, comp.name, name, undefined)) : undefined}
        />
      )}
      {kind.kind === "literal" && !isTable(kind.value) && (
        <div className="flex items-center gap-1">
          <DraftInput<unknown>
            id={id}
            text={literalText(kind.value)}
            disabled={!editable}
            className={cn("num h-7 text-xs", !kind.set && "text-muted-foreground")}
            parse={(typed) => {
              const parsed = parseLiteral(typed, info?.default ?? kind.value);
              return parsed.ok ? commit(parsed.value) : INVALID;
            }}
            onInvalid={() => toast.error(t("acceptance.build.invalidValue"))}
            onCommit={(value) => {
              const same = JSON.stringify(value) === JSON.stringify(kind.set ? kind.value : undefined);
              if (!same) onChange(setParam(structure, scope, comp.name, name, value));
            }}
          />
          {editable && choices.length > 0 && (
            <Select value="" onValueChange={(v) => onChange(setParam(structure, scope, comp.name, name, "$" + v))}>
              <SelectTrigger
                className="size-7 shrink-0 justify-center px-0 [&>svg:last-child]:hidden"
                size="sm"
                aria-label={t("acceptance.build.bind")}
                title={t("acceptance.build.bind")}
                data-testid={`bind-${comp.name}-${name}`}
              >
                <Link2 className="size-3.5" />
              </SelectTrigger>
              <SelectContent position="popper" align="end">
                {choices.map((c) => (
                  <SelectItem key={c} value={c} className="num">
                    {c}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          {editable && kind.set && (
            <Button
              variant="ghost"
              size="icon"
              className="size-7 shrink-0"
              title={t("acceptance.build.resetDefault")}
              aria-label={t("acceptance.build.resetDefault")}
              onClick={() => onChange(setParam(structure, scope, comp.name, name, undefined))}
            >
              <Trash2 className="size-3.5" />
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

/** A table parameter row by row (#1214): key and value, a row added at the end, a row removed.
 * Number keys are typed plainly (or with a prefix) and saved as numbers. */
function TableEditor({
  id,
  table,
  editable,
  isDefault,
  onChange,
  onReset,
}: {
  id: string;
  table: Record<string, string | number | boolean>;
  editable: boolean;
  isDefault: boolean;
  onChange: (next: Record<string, unknown>) => void;
  onReset?: () => void;
}) {
  const { t } = useTranslation();
  const rows = Object.entries(table);
  const like = rows[0]?.[1] ?? 0;
  const [newKey, setNewKey] = useState("");
  const add = () => {
    const key = tableKey(newKey);
    if (!key || key in table) return void toast.error(t("acceptance.build.tableKeyInvalid"));
    onChange(setTableRow(table, key, like));
    setNewKey("");
  };
  return (
    <div className={cn("rounded-md border", isDefault && "text-muted-foreground")} id={id} data-testid={`table-${id}`}>
      <table className="num w-full text-[11px]">
        <thead>
          <tr className="text-muted-foreground border-b">
            <th className="px-2 py-0.5 text-left font-normal">{t("acceptance.build.tableKey")}</th>
            <th className="px-2 py-0.5 text-left font-normal">{t("acceptance.build.tableValue")}</th>
            <th className="w-6" />
          </tr>
        </thead>
        <tbody>
          {rows.map(([k, v]) => (
            <tr key={k} className="border-b last:border-0">
              <td className="px-1 py-0.5">
                <DraftInput<string>
                  text={literalText(parseNumber(k) ?? k)}
                  disabled={!editable}
                  className="h-6 border-0 px-1 text-[11px] shadow-none"
                  aria-label={t("acceptance.build.tableKeyOf", { key: k })}
                  parse={(typed) => {
                    const key = tableKey(typed);
                    return key && (key === k || !(key in table)) ? commit(key) : INVALID;
                  }}
                  onInvalid={() => toast.error(t("acceptance.build.tableKeyInvalid"))}
                  onCommit={(key) => key !== k && onChange(setTableRow(table, k, v, key))}
                />
              </td>
              <td className="px-1 py-0.5">
                <DraftInput<unknown>
                  text={literalText(v)}
                  disabled={!editable}
                  className="h-6 border-0 px-1 text-[11px] shadow-none"
                  aria-label={t("acceptance.build.tableValueOf", { key: k })}
                  parse={(typed) => {
                    const p = parseLiteral(typed, v);
                    return p.ok && p.value !== undefined ? commit(p.value) : INVALID;
                  }}
                  onInvalid={() => toast.error(t("acceptance.build.invalidValue"))}
                  onCommit={(value) => value !== v && onChange(setTableRow(table, k, value))}
                />
              </td>
              <td className="pr-1">
                {editable && (
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-5"
                    aria-label={t("acceptance.build.tableRemove", { key: k })}
                    title={t("acceptance.build.tableRemove", { key: k })}
                    onClick={() => onChange(removeTableRow(table, k))}
                  >
                    <Trash2 className="size-3" />
                  </Button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {editable && (
        <div className="flex items-center gap-1 border-t p-1">
          <Input
            value={newKey}
            onChange={(e) => setNewKey(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && add()}
            placeholder={t("acceptance.build.tableNewKey")}
            aria-label={t("acceptance.build.tableNewKey")}
            className="num h-6 flex-1 text-[11px]"
          />
          <Button variant="ghost" size="icon" className="size-6" onClick={add} disabled={!newKey.trim()} aria-label={t("acceptance.build.tableAdd")} title={t("acceptance.build.tableAdd")}>
            <Plus className="size-3" />
          </Button>
          {onReset && (
            <Button variant="ghost" size="icon" className="size-6" onClick={onReset} aria-label={t("acceptance.build.resetDefault")} title={t("acceptance.build.resetDefault")}>
              <RotateCcw className="size-3" />
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

function PanelPorts({
  structure,
  docs,
  editable,
  onChange,
}: {
  structure: Structure;
  docs: ReadonlyMap<string, ComponentDoc>;
  editable: boolean;
  onChange: (next: Structure | null) => boolean;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState("");
  const [target, setTarget] = useState<string>("");
  // A target is "scope|component.port": electrical pins and the like of every component.
  const targets = (["instrument", "channel"] as const).flatMap((scope) =>
    (scope === "instrument" ? structure.components : structure.channel_components).flatMap((c) =>
      (docs.get(c.type)?.ports ?? []).map((p) => ({ scope, value: `${scope}|${c.name}.${p.name}`, label: `${c.name}.${p.name}` })),
    ),
  );
  const rows = (["instrument", "channel"] as const).flatMap((scope) =>
    Object.entries(structure.ports[scope]).map(([port, to]) => ({ scope, port, to })),
  );
  const add = () => {
    const [scope, to] = target.split("|") as [Scope, string];
    const port = panelPortName(name, scope);
    if (!port || !to) {
      toast.error(t("acceptance.build.panelInvalid"));
      return;
    }
    if (port in structure.ports[scope]) {
      toast.error(t("acceptance.build.panelTaken"));
      return;
    }
    if (onChange(setPanelPort(structure, scope, port, to))) setName("");
  };
  return (
    <div className="space-y-3 p-3 text-xs" data-testid="build-panel-ports">
      <section className="space-y-1.5">
        <h2 className="flex items-center gap-1.5 font-medium">
          {t("acceptance.build.panelPorts")}
          <Hint text={`${t("acceptance.build.panelPortsHint")}。${t("acceptance.build.selectHint")}`} className="ml-auto" />
        </h2>
        {!rows.length && <p className="text-muted-foreground">{t("acceptance.build.panelNone")}</p>}
        <ul className="space-y-1">
          {rows.map((r) => (
            <li key={`${r.scope}-${r.port}`} className="num flex items-center gap-1.5">
              <span className="font-medium">{r.port}</span>
              <span className="text-muted-foreground truncate">→ {r.to}</span>
              {r.scope === "channel" && (
                <Tip label={t("acceptance.build.panelChannel")}>
                  <span role="img" aria-label={t("acceptance.build.panelChannel")} className="text-muted-foreground inline-flex">
                    <Layers className="size-3" />
                  </span>
                </Tip>
              )}
              {editable && (
                <Button
                  variant="ghost"
                  size="icon"
                  className="ml-auto size-6"
                  aria-label={t("acceptance.build.removePort", { name: r.port })}
                  title={t("acceptance.build.removePort", { name: r.port })}
                  onClick={() => onChange(removePanelPort(structure, r.scope, r.port))}
                >
                  <Trash2 className="size-3.5" />
                </Button>
              )}
            </li>
          ))}
        </ul>
      </section>
      {editable && targets.length > 0 && (
        <section className="space-y-1.5 border-t pt-3">
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && add()}
            placeholder={t("acceptance.build.panelName")}
            aria-label={t("acceptance.build.panelName")}
            className="num h-7 text-xs"
          />
          <Select value={target} onValueChange={setTarget}>
            <SelectTrigger className="num h-7 w-full text-xs" size="sm" aria-label={t("acceptance.build.panelTarget")}>
              <SelectValue placeholder={t("acceptance.build.panelTarget")} />
            </SelectTrigger>
            <SelectContent>
              {targets.map((o) => (
                <SelectItem key={o.value} value={o.value} className="num">
                  {o.label}
                  {o.scope === "channel" ? ` · ${t("acceptance.build.panelChannel")}` : ""}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button size="sm" className="h-7" onClick={add} disabled={!name.trim() || !target}>
            <Plus className="size-3.5" /> {t("acceptance.build.panelAdd")}
          </Button>
        </section>
      )}
    </div>
  );
}
