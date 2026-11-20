import { useQueryClient } from "@tanstack/react-query";
import { Blocks, ClipboardCheck, LayoutGrid, PanelRightClose, PanelRightOpen, PowerOff, WifiOff, X } from "lucide-react";
import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { usePanelRef } from "react-resizable-panels";
import { useNavigate, useParams } from "react-router-dom";
import { ApiError, type BenchDocument, type EditOp, type InstrumentInfo, type SessionInfo } from "@/api/client";
import { useEventStream, useEvents, useWallClock } from "@/api/events";
import type { Event } from "@/api/generated/events";
import { keys, useBenchDoc, useBenchPreview, useCatalog, useFaults, useNetLevels, useSession, useSessions, useStartSession } from "@/api/hooks";
import { ErrorState } from "@/components/app/StateViews";
import { Button } from "@/components/ui/button";
import { AssistantPanel } from "@/features/assistant/AssistantPanel";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { Skeleton } from "@/components/ui/skeleton";
import { load, save } from "@/lib/storage";
import { cn } from "@/lib/utils";
import { BottomPanel } from "./BottomPanel";
import { Canvas, type LibraryItem } from "./canvas/Canvas";
import { ClassIcon, classScreen } from "./classes";
import type { Issue } from "./canvas/InstrumentNode";
import { addItem } from "./edit/actions";
import { HistoryDialog } from "./edit/HistoryDialog";
import { EditInspector } from "./edit/EditInspector";
import { applyOps, bindEditor, syncDocument, undo, useEditor } from "./edit/editor";
import i18n from "@/i18n";
import { LibraryPanel } from "./edit/LibraryPanel";
import { CommandPalette } from "./CommandPalette";
import { Inspector } from "./Inspector";
import { autoBottomTab, instrumentTitle } from "./model";
import { ResourceTree } from "./ResourceTree";
import { InternalsView } from "./internals/InternalsView";
import { InstrumentAcceptance } from "@/features/instrument/InstrumentAcceptance";
import { LiveValuesBanner } from "./edit/LiveValuesBanner";
import { RemoteBanner } from "./RemoteBanner";
import { OnScreen } from "./onScreen";
import { PanelScreen } from "./screens/PanelScreen";
import { ReadingsScreen } from "./screens/ReadingsScreen";
import { type Mode, type Tab, tabKey, tabKindOf, useWorkbench } from "./store";
import { TopBar } from "./TopBar";
import { toast } from "sonner";
import { isCancelled } from "@/components/app/ClientsConfirm";

/** The log's tab bar alone (its collapsed height, px), and the share of the height it opens to. */
const BOTTOM_BAR = 37;
const BOTTOM_OPEN = "28%";

export function WorkbenchPage() {
  const { sid = "" } = useParams();
  const { t } = useTranslation();
  const qc = useQueryClient();
  const session = useSession(sid);
  const reset = useWorkbench((s) => s.reset);
  useEffect(() => reset(), [sid, reset]);
  useRememberedScreens(session.data);

  // State events patch the cached session at once; a debounced refetch keeps error queues in step.
  const refetchTimer = useRef<number | undefined>(undefined);
  const onState = useCallback(
    (ev: Event) => {
      qc.setQueryData<SessionInfo>(keys.session(sid), (s) => {
        if (!s || !ev.instrument || !ev.path) return s;
        return {
          ...s,
          instruments: s.instruments.map((i) =>
            i.name !== ev.instrument ? i : { ...i, state: i.state.map((v) => (v.path === ev.path ? { ...v, value: ev.new } : v)) },
          ),
        };
      });
    },
    [qc, sid],
  );
  useEventStream(sid, onState);
  useWallClock(session.data?.clock_mode ?? "", session.data?.now_ns ?? 0, session.data?.time_scale ?? 1);
  const version = useEvents((s) => s.version);
  useEffect(() => {
    window.clearTimeout(refetchTimer.current);
    refetchTimer.current = window.setTimeout(() => {
      void qc.invalidateQueries({ queryKey: keys.session(sid) });
      void qc.invalidateQueries({ queryKey: keys.faults(sid) });
    }, 250);
    return () => window.clearTimeout(refetchTimer.current);
  }, [version, qc, sid]);

  const status = useEvents((s) => s.status);
  const mode = useWorkbench((s) => s.mode);
  const assistantOpen = useWorkbench((s) => s.assistantOpen);
  const setAssistant = useWorkbench((s) => s.setAssistant);
  // The bench this page shows, remembered when its session stops (the assistant may stop it to
  // change its structure, #874): the page then waits for the bench to start again.
  const [lastBench, setLastBench] = useState<string | null>(null);
  const shownBench = session.data?.bench_path;
  if (shownBench && shownBench !== lastBench) setLastBench(shownBench);

  // The bench file behind the session: the workspace stream re-keys it (issue #1484), so an
  // edit made elsewhere (the agent, another tab, an editor) shows up without a poll.
  const benchPath = session.data?.bench_path ?? null;
  useEffect(() => bindEditor(qc), [qc]);
  const docQuery = useBenchDoc(benchPath);
  useEffect(() => {
    if (docQuery.data) syncDocument(docQuery.data);
  }, [docQuery.data]);
  const editorDoc = useEditor((s) => s.doc);
  const doc = editorDoc && editorDoc.path === benchPath ? editorDoc : null;
  const preview = useBenchPreview(benchPath, mode === "edit" ? doc?.sha256 : undefined);

  const inspector = useInspectorFold();

  // The log under the bench: collapsed to its tab bar until there is something to read.
  const bottom = usePanelRef();
  const [bottomOpen, setBottomOpen] = useState(false);
  const hasLog = useEvents((st) => st.events.length > 0);
  const hasComms = useEvents((st) => st.events.some((e) => e.kind === "scpi"));
  const hasOthers = useEvents((st) => st.events.some((e) => e.kind !== "scpi"));
  const openBottomOn = useWorkbench((st) => st.openBottomOn);
  const hasProblems = (doc?.diagnostics.length ?? 0) > 0 || (session.data?.instruments.some((i) => i.errors.length > 0) ?? false);
  const openedOnce = useRef(false);
  const ready = !!session.data;
  // The internals and acceptance pages need the height (#1258): the log folds to its tab bar while
  // one is the active tab and opens again on the canvas or a screen. Opening it there by hand is
  // kept until the next switch; a log that was folded by the user stays folded.
  const tabKind = useWorkbench((st) => tabKindOf(st.active));
  const tall = tabKind === "internals" || tabKind === "acceptance";
  const foldedForPage = useRef(false);
  useEffect(() => {
    const p = bottom.current;
    if (!p) return;
    if (tall && !p.isCollapsed()) {
      p.collapse();
      foldedForPage.current = true;
    } else if (!tall && foldedForPage.current) {
      foldedForPage.current = false;
      if (p.isCollapsed()) p.resize(BOTTOM_OPEN);
    }
  }, [tall, bottom]);
  useEffect(() => {
    if (!ready || openedOnce.current || !(hasLog || hasProblems) || !bottom.current) return;
    openedOnce.current = true;
    openBottomOn(autoBottomTab({ comms: hasComms, events: hasOthers, problems: hasProblems }));
    if (tall) foldedForPage.current = true; // opens when the user comes back to the bench
    else if (bottom.current.isCollapsed()) bottom.current.resize(BOTTOM_OPEN);
  }, [ready, hasLog, hasProblems, hasComms, hasOthers, openBottomOn, bottom, tall]);

  if (session.isLoading) {
    return (
      <div className="flex h-full flex-col">
        <Skeleton className="h-11 w-full rounded-none" />
        <div className="flex flex-1 gap-2 p-2">
          <Skeleton className="h-full w-56" />
          <Skeleton className="h-full flex-1" />
          <Skeleton className="h-full w-72" />
        </div>
      </div>
    );
  }
  const gone = session.error instanceof ApiError && session.error.status === 404;
  if (gone && lastBench) {
    return <StoppedBench sid={sid} bench={lastBench} assistantOpen={assistantOpen} onAssistant={setAssistant} />;
  }
  if (session.error || !session.data) {
    return (
      <div className="flex h-full items-center justify-center">
        <ErrorState error={session.error} onRetry={() => void session.refetch()} />
      </div>
    );
  }
  const s = session.data;
  const readonly = status !== "connected";
  const editing = mode === "edit" && !!doc;
  const view = editing ? (preview.data?.session ?? null) : s;

  return (
    <div className="flex h-full flex-col">
      <TopBar session={s} />
      {status === "reconnecting" && (
        <div className="bg-error/10 text-error flex h-7 shrink-0 items-center justify-center gap-2 border-b text-xs" role="status">
          <WifiOff className="size-3.5" /> {t("connection.reconnecting")}
        </div>
      )}
      <div className="flex min-h-0 flex-1">
        <ResizablePanelGroup orientation="vertical" className="min-h-0 min-w-0 flex-1">
        <ResizablePanel minSize="30%">
          <ResizablePanelGroup orientation="horizontal">
            <ResizablePanel defaultSize="16%" minSize="10%" maxSize="30%" className="bg-sidebar">
              <LeftPanel session={view ?? s} mode={editing ? "edit" : "run"} />
            </ResizablePanel>
            <ResizableHandle />
            <ResizablePanel defaultSize="60%" minSize="30%" data-testid="center-panel">
              <CenterTabs
                inspectorShown={inspector.shown}
                onToggleInspector={inspector.toggle}
                session={s}
                view={view}
                doc={doc}
                editing={editing}
                previewError={editing ? (preview.data?.error ?? null) : null}
                omitted={editing ? (preview.data?.omitted ?? []) : []}
                readonly={readonly}
              />
            </ResizablePanel>
            <ResizableHandle />
            <ResizablePanel
              panelRef={inspector.panel}
              collapsible
              collapsedSize={0}
              defaultSize={inspector.shown ? `${inspector.initialSize}%` : 0}
              minSize="16%"
              maxSize="40%"
              className="bg-sidebar"
              data-testid="inspector-panel"
              onResize={inspector.onResize}
            >
              {inspector.shown &&
                (editing && doc ? <EditInspector view={view} doc={doc} /> : <Inspector session={s} readonly={readonly} />)}
            </ResizablePanel>
          </ResizablePanelGroup>
        </ResizablePanel>
        <ResizableHandle />
        {/* The log starts as its tab bar: an empty table would take a quarter of the height from the
            bench. It opens by itself the first time there is something to read (a command, an
            event, a problem), and from then on its size is the user's. */}
        <ResizablePanel
          panelRef={bottom}
          collapsible
          collapsedSize={BOTTOM_BAR}
          defaultSize={BOTTOM_BAR}
          data-testid="bottom-panel"
          minSize="12%"
          onResize={(size) => setBottomOpen(size.inPixels > BOTTOM_BAR + 4)}
        >
          <BottomPanel
            session={s}
            diagnostics={doc?.diagnostics}
            open={bottomOpen}
            onToggle={() => (bottomOpen ? bottom.current?.collapse() : bottom.current?.resize(BOTTOM_OPEN))}
          />
        </ResizablePanel>
        </ResizablePanelGroup>
        {assistantOpen && s.bench_path && <AssistantAside bench={s.bench_path} onClose={() => setAssistant(false)} />}
      </div>
      <CommandPalette session={s} />
      {s.bench_path && <HistoryDialog path={s.bench_path} />}
    </div>
  );
}

/** The assistant beside the workbench; on a narrow window it covers the page (#877). */
function AssistantAside({ bench, onClose }: { bench: string; onClose: () => void }) {
  return (
    <div className="bg-sidebar fixed inset-0 z-40 border-l md:static md:z-auto md:w-[360px] md:shrink-0 xl:w-[400px]">
      <AssistantPanel bench={bench} onClose={onClose} />
    </div>
  );
}

/** The bench's session stopped (by the assistant, after asking): the conversation stays, and the
 * page follows the bench as soon as it runs again. */
function StoppedBench({ sid, bench, assistantOpen, onAssistant }: { sid: string; bench: string; assistantOpen: boolean; onAssistant: (open: boolean) => void }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const sessions = useSessions();
  const start = useStartSession();
  const next = sessions.data?.find((x) => x.bench_path === bench);
  useEffect(() => {
    // follow a *new* session of the same bench: while the stale cache still lists the session
    // that just stopped, re-navigating to it would race the "back home" of a manual close (#913)
    if (next && next.id !== sid) navigate(`/s/${next.id}`, { replace: true });
  }, [next, sid, navigate]);
  return (
    <div className="flex h-full">
      <div className="flex min-w-0 flex-1 items-center justify-center p-6" data-testid="bench-stopped">
        <div className="flex max-w-md flex-col items-center gap-3 text-center">
          <PowerOff className="text-muted-foreground size-6" aria-hidden />
          <p className="font-medium">{t("assistant.stoppedBench.title")}</p>
          <p className="text-muted-foreground text-[12.5px]">{t("assistant.stoppedBench.body", { path: bench })}</p>
          <div className="flex flex-wrap justify-center gap-2">
            <Button size="sm" disabled={start.isPending} onClick={() =>
                start.mutate(bench, {
                  onSuccess: (s) => navigate(`/s/${s.id}`, { replace: true }),
                  onError: (e) => !isCancelled(e) && toast.error(t("errors.generic"), { description: (e as Error).message }),
                })
              }
            >
              {t("assistant.stoppedBench.start")}
            </Button>
            {!assistantOpen && (
              <Button size="sm" variant="outline" onClick={() => onAssistant(true)}>
                {t("assistant.open")}
              </Button>
            )}
            <Button size="sm" variant="ghost" onClick={() => navigate("/")}>
              {t("workbench.backHome")}
            </Button>
          </div>
        </div>
      </div>
      {assistantOpen && <AssistantAside bench={bench} onClose={() => onAssistant(false)} />}
    </div>
  );
}

function LeftPanel({ session, mode }: { session: SessionInfo; mode: Mode }) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<"library" | "resources">("library");
  if (mode !== "edit") return <ResourceTree session={session} />;
  return (
    <div className="flex h-full flex-col">
      <div role="tablist" className="flex h-9 shrink-0 items-end gap-0.5 border-b px-1">
        {(["library", "resources"] as const).map((k) => (
          <button
            key={k}
            role="tab"
            aria-selected={tab === k}
            onClick={() => setTab(k)}
            className={cn(
              "h-8 rounded-t-md border border-b-0 px-3 text-xs",
              tab === k ? "bg-background text-foreground -mb-px" : "text-muted-foreground hover:text-foreground border-transparent",
            )}
          >
            {k === "library" ? t("edit.library") : t("workbench.resources")}
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1">{tab === "library" ? <LibraryPanel /> : <ResourceTree session={session} />}</div>
    </div>
  );
}

function issuesOf(doc: BenchDocument | null): Map<string, Issue> {
  const out = new Map<string, Issue>();
  for (const d of doc?.diagnostics ?? []) {
    if (!d.subject) continue;
    if (d.severity === "error" || !out.has(d.subject)) out.set(d.subject, d.severity);
  }
  return out;
}

function CenterTabs({
  inspectorShown,
  onToggleInspector,
  session,
  view,
  doc,
  editing,
  previewError,
  omitted,
  readonly,
}: {
  inspectorShown: boolean;
  onToggleInspector: () => void;
  session: SessionInfo;
  view: SessionInfo | null;
  doc: BenchDocument | null;
  editing: boolean;
  previewError: string | null;
  omitted: string[];
  readonly: boolean;
}) {
  const { t } = useTranslation();
  const { tabs, active, setActive, closeTab } = useWorkbench();
  const label = (tab: Tab) => {
    if (tab.kind === "canvas") return t("workbench.canvas");
    const inst = session.instruments.find((i) => i.name === tab.instrument);
    const name = inst ? instrumentTitle(inst) : tab.instrument;
    if (tab.kind === "internals") return t("internals.tab", { name });
    if (tab.kind === "acceptance") return t("acceptance.tab", { name });
    return name;
  };
  const icon = (tab: Tab) => {
    if (tab.kind === "canvas") return <LayoutGrid className="size-3.5" />;
    if (tab.kind === "internals") return <Blocks className="size-3.5" />;
    if (tab.kind === "acceptance") return <ClipboardCheck className="size-3.5" />;
    const cls = session.instruments.find((i) => i.name === tab.instrument)?.instrument_class;
    return <ClassIcon cls={cls} className="size-3.5" />;
  };
  const current = tabs.find((x) => tabKey(x) === active) ?? tabs[0];
  return (
    <div className="flex h-full flex-col">
      <div role="tablist" className="bg-sidebar flex h-9 shrink-0 items-end gap-0.5 overflow-x-auto border-b px-1">
        {tabs.map((tab) => {
          const key = tabKey(tab);
          const on = key === active;
          return (
            <div
              key={key}
              role="tab"
              aria-selected={on}
              tabIndex={0}
              onClick={() => setActive(key)}
              onKeyDown={(e) => e.key === "Enter" && setActive(key)}
              className={cn(
                "group flex h-8 cursor-default items-center gap-1.5 rounded-t-md border border-b-0 px-3 text-xs",
                on ? "bg-background text-foreground -mb-px" : "text-muted-foreground hover:text-foreground border-transparent",
              )}
            >
              {icon(tab)}
              <span className="max-w-40 truncate">{label(tab)}</span>
              {tab.kind !== "canvas" && (
                <button
                  className="hover:bg-accent -mr-1 rounded p-0.5 opacity-60 group-hover:opacity-100"
                  onClick={(e) => {
                    e.stopPropagation();
                    closeTab(key);
                  }}
                  aria-label={t("common.close")}
                >
                  <X className="size-3" />
                </button>
              )}
            </div>
          );
        })}
        <button
          type="button"
          className="text-muted-foreground hover:text-foreground hover:bg-accent sticky right-0 mb-1 ml-auto shrink-0 rounded p-1"
          onClick={onToggleInspector}
          aria-pressed={inspectorShown}
          aria-label={t("workbench.inspectorToggle")}
          title={t("workbench.inspectorToggle")}
          data-testid="inspector-toggle"
        >
          {inspectorShown ? <PanelRightClose className="size-4" /> : <PanelRightOpen className="size-4" />}
        </button>
      </div>
      <div className="min-h-0 flex-1">
        {current.kind === "canvas" ? (
          <BenchCanvas session={session} view={view} doc={doc} editing={editing} previewError={previewError} omitted={omitted} />
        ) : current.kind === "internals" ? (
          <Internals session={session} instrument={current.instrument} />
        ) : current.kind === "acceptance" ? (
          <Acceptance session={session} tab={current} />
        ) : (
          <div className="flex h-full flex-col">
            {editing && (() => {
              const live = session.instruments.find((i) => i.name === current.instrument);
              const initial = view?.instruments.find((i) => i.name === current.instrument);
              return live ? <LiveValuesBanner live={live} initial={initial} panel={!!live.display.panel} /> : null;
            })()}
            {(() => {
              const inst = session.instruments.find((i) => i.name === current.instrument);
              return inst ? <RemoteBanner sid={session.id} inst={inst} /> : null;
            })()}
            <div className="min-h-0 flex-1">
              <Screen
                session={session}
                instrument={current.instrument}
                initial={editing ? view?.instruments.find((i) => i.name === current.instrument) : undefined}
                readonly={readonly}
              />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function Internals({ session, instrument }: { session: SessionInfo; instrument: string }) {
  const inst = session.instruments.find((i) => i.name === instrument);
  return inst ? <InternalsView key={inst.name} session={session} inst={inst} /> : null;
}

/**
 * The instrument's definition acceptance page, as a workbench tab beside the internals view. A copy
 * made there ("copy as my instrument") opens in the same tab at the same section, and the bench is
 * offered the copy for this instrument (#1250).
 */
function Acceptance({ session, tab }: { session: SessionInfo; tab: Extract<Tab, { kind: "acceptance" }> }) {
  const showDefinition = useWorkbench((s) => s.showDefinition);
  const inst = session.instruments.find((i) => i.name === tab.instrument);
  if (!inst) return null;
  const definitionId = tab.definition ?? inst.definition_id;
  const onOpenCopy = (id: string, section: string) => {
    showDefinition(inst.name, id, section);
    if (id !== inst.definition_id && session.bench_path) offerCopy(inst.name, id);
  };
  const page = `/i/${definitionId}?${new URLSearchParams({ from: session.id }).toString()}`;
  return <InstrumentAcceptance key={definitionId} definitionId={definitionId} initialSection={tab.section} onOpenCopy={onOpenCopy} pageHref={page} />;
}

/** Ask whether the bench should use the copy for this instrument; yes swaps its model in the bench file, with an undo (#1250). */
function offerCopy(name: string, id: string) {
  const t = i18n.t.bind(i18n);
  toast(t("acceptance.useCopy", { name }), {
    id: `use-copy-${name}`,
    description: t("acceptance.useCopyBody", { name, id }),
    duration: 15000,
    action: {
      label: t("acceptance.useCopyConfirm"),
      onClick: () =>
        void applyOps([{ op: "replace_instrument", name, model: id }]).then((doc) => {
          if (doc) toast.success(t("acceptance.usedCopy", { name, id }), { action: { label: t("edit.undo"), onClick: () => void undo() } });
        }),
    },
  });
}

function Screen({
  session,
  instrument,
  initial,
  readonly,
}: {
  session: SessionInfo;
  instrument: string;
  /** In build mode, the instrument as the bench file starts it: a panel sets these (#103). */
  initial?: InstrumentInfo;
  readonly: boolean;
}) {
  const inst = session.instruments.find((i) => i.name === instrument);
  if (!inst) return null;
  // The class template picks the screen (D1); instruments without one get the generic layout.
  const template = inst.display.template ?? inst.instrument_class;
  // Otherwise the definition's panel, or its class's default (multimeter, calibrator); else the generic layout.
  const screen =
    classScreen(template, { session, inst, readonly }) ??
    (inst.display.panel ? (
      <PanelScreen sid={session.id} inst={inst} initial={initial} readonly={readonly} />
    ) : (
      <ReadingsScreen sid={session.id} inst={inst} readonly={readonly} />
    ));
  // The fields on it are left out of the inspector beside it (#1229).
  return <OnScreen.Provider value={!initial}>{screen}</OnScreen.Provider>;
}

function BenchCanvas({
  session,
  view,
  doc,
  editing,
  previewError,
  omitted,
}: {
  session: SessionInfo;
  view: SessionInfo | null;
  doc: BenchDocument | null;
  editing: boolean;
  previewError: string | null;
  omitted: string[];
}) {
  const { t } = useTranslation();
  const catalog = useCatalog().data;
  const partUnits = useMemo(() => Object.fromEntries((catalog?.components ?? []).map((c) => [c.type, c.units])), [catalog]);
  const issues = useMemo(() => (editing ? issuesOf(doc) : undefined), [editing, doc]);
  const faults = useFaults(session.id).data;
  const faulted = useMemo(
    () =>
      editing
        ? undefined
        : new Set(
            (faults ?? [])
              .filter((f) => f.status === "active")
              .map((f) => f.spec.instrument ?? f.spec.component?.split("/")[0] ?? ""),
          ),
    [editing, faults],
  );
  // Live levels only for the running bench: an edited preview may differ from what is running.
  const version = useDeferredValue(useEvents((s) => s.version));
  const levelData = useNetLevels(session.id, version, !editing).data;
  const levels = useMemo(
    () => (editing || !levelData ? undefined : new Map(levelData.nets.map((n) => [n.net, n]))),
    [editing, levelData],
  );
  const layout = (doc?.data.layout as Record<string, { x: number; y: number }> | undefined) ?? undefined;
  // Node positions are part of the bench file (D8); moving a node never needs a restart.
  const onOps = useMemo(() => (session.bench_path ? (ops: EditOp[]) => void applyOps(ops) : undefined), [session.bench_path]);
  const onDropItem = useCallback(
    (item: LibraryItem, at: { x: number; y: number }) => catalog && void addItem(catalog, item, at),
    [catalog],
  );
  if (!view) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        {previewError ? (
          <ErrorState error={new Error(t("edit.previewError", { error: previewError }))} />
        ) : (
          <Skeleton className="h-2/3 w-2/3" />
        )}
      </div>
    );
  }
  return (
    <div className="relative h-full">
      <Canvas view={view} layout={layout} editable={editing} onOps={onOps} onDropItem={onDropItem} issues={issues} faulted={faulted} levels={levels} partUnits={partUnits} />
      {editing && (
        <div
          className="bg-card/90 text-muted-foreground pointer-events-none absolute top-2 left-2 max-w-72 rounded-md border px-2 py-1 text-[11px] shadow-sm"
          role="note"
          aria-label={t("edit.initialTitle")}
        >
          <span className="text-foreground font-medium">{t("edit.initialTitle")}</span> · {t("edit.initialNote")}
        </div>
      )}
      {editing && (
        <div className="pointer-events-none absolute top-2 left-1/2 flex -translate-x-1/2 flex-col items-center gap-1">
          {omitted.length > 0 && (
            <div className="bg-error/10 text-error rounded-md border px-2 py-1 text-[11px]">
              {t("edit.omitted", { count: omitted.length, names: omitted.join("、") })}
            </div>
          )}
          {view.instruments.length + view.parts.length === 0 && (
            <div className="bg-card text-muted-foreground mt-24 rounded-md border px-3 py-2 text-xs shadow-sm">{t("edit.libraryHint")}</div>
          )}
          {view.wires.length === 0 && view.instruments.length + view.parts.length > 1 && (
            <div className="bg-card text-muted-foreground rounded-md border px-2 py-1 text-[11px] shadow-sm">{t("edit.connectHint")}</div>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * The inspector beside the current tab (#1221): shown or folded away by the kind of tab (the
 * store's ``inspector``, the user's choice per kind), and shown for the moment when something asks
 * it to show a setting (the internals view's "locate", #800) or the user selects something, until
 * another tab is chosen. Dragging
 * its edge shut or open is a choice for that kind of tab, like the button.
 */
function useInspectorFold() {
  const kind = useWorkbench((s) => tabKindOf(s.active));
  const shown = useWorkbench(inspectorShown);
  const panel = usePanelRef();
  // The width for this kind of tab (#1229), read once when the panel first lays out.
  const [initialSize] = useState(() => useWorkbench.getState().inspectorSize[kind]);
  useEffect(() => {
    const p = panel.current;
    if (!p) return;
    if (!shown) {
      if (!p.isCollapsed()) p.collapse();
      return;
    }
    // Read before expanding: expanding reports the width it had beside the previous kind of tab.
    const want = useWorkbench.getState().inspectorSize[kind];
    if (p.isCollapsed()) p.expand();
    if (Math.abs(p.getSize().asPercentage - want) > 0.5) p.resize(`${want}%`);
  }, [shown, kind, panel]);
  const onResize = useCallback(
    (size: { asPercentage: number }) => {
      const open = size.asPercentage > 0;
      const st = useWorkbench.getState();
      if (open !== inspectorShown(st)) st.setInspector(kind, open); // else: the fold asked for above
      else if (open) st.setInspectorSize(kind, size.asPercentage);
    },
    [kind],
  );
  const toggle = useCallback(() => {
    const st = useWorkbench.getState();
    st.setInspector(kind, !inspectorShown(st));
  }, [kind]);
  return { shown, panel, onResize, toggle, initialSize };
}

const inspectorShown = (s: { active: string; inspector: Record<string, boolean>; lentTo: string | null }) =>
  s.inspector[tabKindOf(s.active)] || s.lentTo === s.active;

/**
 * The screens open on a bench are remembered per bench file in this browser and reopened the next
 * time it starts, so a scope screen does not have to be opened by hand every time.
 */
function useRememberedScreens(session: SessionInfo | undefined) {
  const restore = useWorkbench((s) => s.restore);
  const key = session ? `screens:${session.bench_path ?? session.bench_name}` : null;
  // Restored once per session (a new start of the same bench restores again, after the reset).
  const marker = session && key ? `${session.id}|${key}` : null;
  const restored = useRef<string | null>(null);
  const names = session?.instruments.map((i) => i.name).join("\n");
  useEffect(() => {
    if (!key || !marker || names === undefined || restored.current === marker) return;
    restored.current = marker;
    const known = new Set(names.split("\n"));
    const saved = load<{ tabs: Tab[]; active: string }>(key, { tabs: [], active: "canvas" });
    restore(
      saved.tabs.filter((t) => t.kind === "screen" && known.has(t.instrument)),
      saved.active,
    );
  }, [key, marker, names, restore]);
  const tabs = useWorkbench((s) => s.tabs);
  const active = useWorkbench((s) => s.active);
  useEffect(() => {
    if (key && marker && restored.current === marker) save(key, { tabs: tabs.filter((t) => t.kind === "screen"), active });
  }, [key, marker, tabs, active]);
}
