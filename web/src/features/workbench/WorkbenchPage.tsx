import { useQueryClient } from "@tanstack/react-query";
import { Activity, AudioWaveform, Cpu, LayoutGrid, WifiOff, X } from "lucide-react";
import { useCallback, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useParams } from "react-router-dom";
import type { SessionInfo } from "@/api/client";
import { useEventStream, useEvents } from "@/api/events";
import type { Event } from "@/api/generated/events";
import { keys, useSession } from "@/api/hooks";
import { ErrorState } from "@/components/app/StateViews";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { BottomPanel } from "./BottomPanel";
import { Canvas } from "./canvas/Canvas";
import { CommandPalette } from "./CommandPalette";
import { Inspector } from "./Inspector";
import { instrumentTitle } from "./model";
import { ResourceTree } from "./ResourceTree";
import { GeneratorPanel } from "./screens/GeneratorPanel";
import { ScopeScreen } from "./screens/ScopeScreen";
import { type Tab, tabKey, useWorkbench } from "./store";
import { TopBar } from "./TopBar";

export function WorkbenchPage() {
  const { sid = "" } = useParams();
  const { t } = useTranslation();
  const qc = useQueryClient();
  const session = useSession(sid);
  const reset = useWorkbench((s) => s.reset);
  useEffect(() => reset(), [sid, reset]);

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
  const version = useEvents((s) => s.version);
  useEffect(() => {
    window.clearTimeout(refetchTimer.current);
    refetchTimer.current = window.setTimeout(() => void qc.invalidateQueries({ queryKey: keys.session(sid) }), 250);
    return () => window.clearTimeout(refetchTimer.current);
  }, [version, qc, sid]);

  const status = useEvents((s) => s.status);

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
              <ResourceTree session={s} />
            </ResizablePanel>
            <ResizableHandle />
            <ResizablePanel defaultSize="60%" minSize="30%">
              <CenterTabs session={s} readonly={readonly} />
            </ResizablePanel>
            <ResizableHandle />
            <ResizablePanel defaultSize="24%" minSize="16%" maxSize="40%" className="bg-sidebar">
              <Inspector session={s} readonly={readonly} />
            </ResizablePanel>
          </ResizablePanelGroup>
        </ResizablePanel>
        <ResizableHandle />
        <ResizablePanel defaultSize="28%" minSize="10%">
          <BottomPanel session={s} />
        </ResizablePanel>
      </ResizablePanelGroup>
      <CommandPalette session={s} />
    </div>
  );
}

function CenterTabs({ session, readonly }: { session: SessionInfo; readonly: boolean }) {
  const { t } = useTranslation();
  const { tabs, active, setActive, closeTab } = useWorkbench();
  const label = (tab: Tab) => {
    if (tab.kind === "canvas") return t("workbench.canvas");
    const inst = session.instruments.find((i) => i.name === tab.instrument);
    return inst ? instrumentTitle(inst) : tab.instrument;
  };
  const icon = (tab: Tab) => {
    if (tab.kind === "canvas") return <LayoutGrid className="size-3.5" />;
    const cls = session.instruments.find((i) => i.name === tab.instrument)?.instrument_class;
    const Icon = cls === "oscilloscope" ? Activity : cls === "function_generator" ? AudioWaveform : Cpu;
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
          <Canvas session={session} />
        ) : (
          <Screen session={session} instrument={current.instrument} readonly={readonly} />
        )}
      </div>
    </div>
  );
}

function Screen({ session, instrument, readonly }: { session: SessionInfo; instrument: string; readonly: boolean }) {
  const inst = session.instruments.find((i) => i.name === instrument);
  if (!inst) return null;
  if (inst.instrument_class === "oscilloscope") return <ScopeScreen sid={session.id} inst={inst} />;
  return <GeneratorPanel sid={session.id} inst={inst} readonly={readonly} />;
}
