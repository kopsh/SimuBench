import { useQueryClient } from "@tanstack/react-query";
import { Blocks, Cpu, LayoutGrid, WifiOff, X } from "lucide-react";
import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useParams } from "react-router-dom";
import type { BenchDocument, EditOp, SessionInfo } from "@/api/client";
import { useEventStream, useEvents, useWallClock } from "@/api/events";
import type { Event } from "@/api/generated/events";
import { keys, useBenchDoc, useBenchPreview, useCatalog, useFaults, useNetLevels, useSession } from "@/api/hooks";
import { ErrorState } from "@/components/app/StateViews";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { Skeleton } from "@/components/ui/skeleton";
import { load, save } from "@/lib/storage";
import { cn } from "@/lib/utils";
import { BottomPanel } from "./BottomPanel";
import { Canvas, type LibraryItem } from "./canvas/Canvas";
import { CLASS_ICON } from "./canvas/InstrumentNode";
import type { Issue } from "./canvas/InstrumentNode";
import { addItem } from "./edit/actions";
import { HistoryDialog } from "./edit/HistoryDialog";
import { EditInspector } from "./edit/EditInspector";
import { applyOps, bindEditor, syncDocument, useEditor } from "./edit/editor";
import { LibraryPanel } from "./edit/LibraryPanel";
import { CommandPalette } from "./CommandPalette";
import { Inspector } from "./Inspector";
import { instrumentTitle } from "./model";
import { ResourceTree } from "./ResourceTree";
import { InternalsView } from "./internals/InternalsView";
import { LiveValuesBanner } from "./edit/LiveValuesBanner";
import { GeneratorPanel } from "./screens/GeneratorPanel";
import { LoadScreen } from "./screens/LoadScreen";
import { LockinScreen } from "./screens/LockinScreen";
import { ReadingsScreen } from "./screens/ReadingsScreen";
import { RfGeneratorScreen } from "./screens/RfGeneratorScreen";
import { ScopeScreen } from "./screens/ScopeScreen";
import { SmuScreen } from "./screens/SmuScreen";
import { SpectrumScreen } from "./screens/SpectrumScreen";
import { type Mode, type Tab, tabKey, useWorkbench } from "./store";
import { TopBar } from "./TopBar";

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
  useWallClock(session.data?.clock_mode ?? "", session.data?.now_ns ?? 0);
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

  // The bench file behind the session: polled, so that edits made elsewhere show up.
  const benchPath = session.data?.bench_path ?? null;
  useEffect(() => bindEditor(qc), [qc]);
  const docQuery = useBenchDoc(benchPath);
  useEffect(() => {
    if (docQuery.data) syncDocument(docQuery.data);
  }, [docQuery.data]);
  const editorDoc = useEditor((s) => s.doc);
  const doc = editorDoc && editorDoc.path === benchPath ? editorDoc : null;
  const preview = useBenchPreview(benchPath, mode === "edit" ? doc?.sha256 : undefined);

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
      <ResizablePanelGroup orientation="vertical" className="min-h-0 flex-1">
        <ResizablePanel defaultSize="72%" minSize="30%">
          <ResizablePanelGroup orientation="horizontal">
            <ResizablePanel defaultSize="16%" minSize="10%" maxSize="30%" className="bg-sidebar">
              <LeftPanel session={view ?? s} mode={editing ? "edit" : "run"} />
            </ResizablePanel>
            <ResizableHandle />
            <ResizablePanel defaultSize="60%" minSize="30%">
              <CenterTabs
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
            <ResizablePanel defaultSize="24%" minSize="16%" maxSize="40%" className="bg-sidebar">
              {editing && doc ? <EditInspector view={view} doc={doc} /> : <Inspector session={s} readonly={readonly} />}
            </ResizablePanel>
          </ResizablePanelGroup>
        </ResizablePanel>
        <ResizableHandle />
        <ResizablePanel defaultSize="28%" minSize="10%">
          <BottomPanel session={s} diagnostics={doc?.diagnostics} />
        </ResizablePanel>
      </ResizablePanelGroup>
      <CommandPalette session={s} />
      {s.bench_path && <HistoryDialog path={s.bench_path} />}
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
  session,
  view,
  doc,
  editing,
  previewError,
  omitted,
  readonly,
}: {
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
    return tab.kind === "internals" ? t("internals.tab", { name }) : name;
  };
  const icon = (tab: Tab) => {
    if (tab.kind === "canvas") return <LayoutGrid className="size-3.5" />;
    if (tab.kind === "internals") return <Blocks className="size-3.5" />;
    const cls = session.instruments.find((i) => i.name === tab.instrument)?.instrument_class;
    const Icon = CLASS_ICON[cls ?? ""] ?? Cpu;
    return <Icon className="size-3.5" />;
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
      </div>
      <div className="min-h-0 flex-1">
        {current.kind === "canvas" ? (
          <BenchCanvas session={session} view={view} doc={doc} editing={editing} previewError={previewError} omitted={omitted} />
        ) : current.kind === "internals" ? (
          <Internals session={session} instrument={current.instrument} />
        ) : (
          <div className="flex h-full flex-col">
            {editing && (() => {
              const live = session.instruments.find((i) => i.name === current.instrument);
              return live ? <LiveValuesBanner live={live} initial={view?.instruments.find((i) => i.name === current.instrument)} /> : null;
            })()}
            <div className="min-h-0 flex-1">
              <Screen session={session} instrument={current.instrument} readonly={readonly} />
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

function Screen({ session, instrument, readonly }: { session: SessionInfo; instrument: string; readonly: boolean }) {
  const inst = session.instruments.find((i) => i.name === instrument);
  if (!inst) return null;
  // The class template picks the screen (D1); instruments without one get the generic layout.
  const template = inst.display.template ?? inst.instrument_class;
  if (template === "oscilloscope") return <ScopeScreen session={session} inst={inst} readonly={readonly} />;
  if (template === "function_generator") return <GeneratorPanel sid={session.id} inst={inst} readonly={readonly} />;
  if (template === "lockin") return <LockinScreen sid={session.id} inst={inst} readonly={readonly} />;
  if (template === "spectrum_analyzer") return <SpectrumScreen sid={session.id} inst={inst} readonly={readonly} />;
  if (template === "smu") return <SmuScreen sid={session.id} inst={inst} readonly={readonly} />;
  if (template === "electronic_load") return <LoadScreen sid={session.id} inst={inst} readonly={readonly} />;
  if (template === "rf_generator") return <RfGeneratorScreen sid={session.id} inst={inst} readonly={readonly} />;
  return <ReadingsScreen sid={session.id} inst={inst} readonly={readonly} />;
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
      <Canvas view={view} layout={layout} editable={editing} onOps={onOps} onDropItem={onDropItem} issues={issues} faulted={faulted} levels={levels} />
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
