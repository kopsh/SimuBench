import { ChevronLeft, Command, History, Power, RotateCw, StepForward } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import type { SessionInfo } from "@/api/client";
import { type ConnectionState, useEvents } from "@/api/events";
import { useAdvance, useResetSession, useStopSession } from "@/api/hooks";
import { Logo } from "@/components/app/Logo";
import { isCancelled } from "@/components/app/ClientsConfirm";
import { PreferencesMenu } from "@/components/app/PreferencesMenu";
import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/kbd";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { formatClock, formatEng } from "@/lib/format";
import { cn } from "@/lib/utils";
import { EditControls, benchChanged, useRebuild } from "./edit/EditControls";
import { useEditor } from "./edit/editor";
import { useWorkbench } from "./store";

const STEPS = [1e-3, 10e-3, 100e-3, 1];

export function TopBar({ session }: { session: SessionInfo }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const status = useEvents((s) => s.status);
  const nowNs = Math.max(useEvents((s) => s.nowNs), session.now_ns);
  const setPalette = useWorkbench((s) => s.setPalette);
  const setHistory = useWorkbench((s) => s.setHistory);
  const advance = useAdvance(session.id);
  const stop = useStopSession();
  const reset = useResetSession(session.id);
  // Back in Operate without rebuilding ("later", scripts connected): offer the rebuild here (#121).
  const mode = useWorkbench((s) => s.mode);
  const changed = benchChanged(session, useEditor((s) => s.doc));
  const rebuild = useRebuild(session);
  const [step, setStep] = useState(STEPS[1]);
  const mac = navigator.platform.toLowerCase().includes("mac");

  return (
    <header className="bg-sidebar flex h-11 shrink-0 items-center gap-2 border-b px-2">
      <Tooltip>
        <TooltipTrigger asChild>
          <Button variant="ghost" size="icon" className="size-8" onClick={() => navigate("/")} aria-label={t("workbench.backHome")}>
            <ChevronLeft className="size-4" />
          </Button>
        </TooltipTrigger>
        <TooltipContent>{t("workbench.backHome")}</TooltipContent>
      </Tooltip>
      <Logo className="hidden sm:inline" />
      <Separator orientation="vertical" className="mx-1 !h-5" />
      <div className="min-w-0">
        <div className="truncate text-[13px] leading-tight font-medium">{session.bench_name}</div>
        <div className="text-muted-foreground num truncate text-[11px] leading-tight">{session.bench_path}</div>
      </div>
      <ConnectionBadge status={status} clockMode={session.clock_mode} />
      <Separator orientation="vertical" className="mx-1 !h-5" />
      <EditControls session={session} />
      {mode === "run" && changed && (
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="outline"
              size="sm"
              className="h-8 gap-1.5 border-amber-500/50 text-xs text-amber-700 dark:text-amber-400"
              disabled={rebuild.isPending}
              onClick={rebuild.run}
            >
              <RotateCw className="size-3.5" /> {t("edit.benchChanged")}
            </Button>
          </TooltipTrigger>
          <TooltipContent className="max-w-72">{t("edit.benchChangedHint")}</TooltipContent>
        </Tooltip>
      )}
      {session.stale_definitions.length > 0 && (
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="outline"
              size="sm"
              className="h-8 gap-1.5 border-amber-500/50 text-xs text-amber-700 dark:text-amber-400"
              disabled={reset.isPending}
              onClick={() =>
                reset.mutate(undefined, {
                  onSuccess: () => toast.success(t("workbench.resetDone")),
                  onError: (e) => !isCancelled(e) && toast.error((e as Error).message),
                })
              }
            >
              <RotateCw className="size-3.5" /> {t("workbench.staleDefinitions")}
            </Button>
          </TooltipTrigger>
          <TooltipContent className="max-w-72">
            {t("workbench.staleDefinitionsHint", { ids: session.stale_definitions.join(", ") })}
          </TooltipContent>
        </Tooltip>
      )}

      <div className="mx-auto flex items-center gap-2">
        <div className="bg-background flex h-8 items-center gap-3 rounded-md border px-3" aria-live="polite">
          <span className="text-muted-foreground text-[11px]">{t(`workbench.clockMode.${session.clock_mode}`)}</span>
          <span className="num text-[13px] font-medium" title={t("workbench.clock")}>
            t = {formatClock(nowNs)}
          </span>
        </div>
        {session.clock_mode !== "wall" && (
          <div className="flex items-center">
            <Select value={String(step)} onValueChange={(v) => setStep(Number(v))}>
              <SelectTrigger size="sm" className="num h-8 w-[84px] rounded-r-none text-xs" aria-label={t("workbench.advance")}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {STEPS.map((s) => (
                  <SelectItem key={s} value={String(s)} className="num text-xs">
                    {formatEng(s, "s", 3)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button
              variant="outline"
              size="sm"
              className="h-8 rounded-l-none border-l-0"
              disabled={status !== "connected" || advance.isPending}
              onClick={() => advance.mutate(step, { onError: (e) => !isCancelled(e) && toast.error((e as Error).message) })}
            >
              <StepForward className="size-3.5" /> {t("workbench.advance")}
            </Button>
          </div>
        )}
      </div>

      <Button variant="outline" size="sm" className="text-muted-foreground h-8 gap-2" onClick={() => setPalette(true)}>
        <Command className="size-3.5" />
        <span className="hidden md:inline">{t("workbench.commandPalette")}</span>
        <Kbd>{mac ? "⌘K" : "Ctrl K"}</Kbd>
      </Button>
      {session.bench_path && (
        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant="ghost" size="icon" className="size-8" aria-label={t("history.title")} onClick={() => setHistory(true)}>
              <History className="size-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>{t("history.title")}</TooltipContent>
        </Tooltip>
      )}
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className="size-8"
            aria-label={t("workbench.reset")}
            disabled={reset.isPending}
            onClick={() =>
              reset.mutate(undefined, {
                onSuccess: () => toast.success(t("workbench.resetDone")),
                onError: (e) => !isCancelled(e) && toast.error((e as Error).message),
              })
            }
          >
            <RotateCw className={reset.isPending ? "size-3.5 animate-spin" : "size-3.5"} />
          </Button>
        </TooltipTrigger>
        <TooltipContent>{t("workbench.reset")}</TooltipContent>
      </Tooltip>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className="size-8"
            aria-label={t("workbench.stopSession")}
            onClick={() => stop.mutate(session.id, { onSuccess: () => navigate("/") })}
          >
            <Power className="size-3.5" />
          </Button>
        </TooltipTrigger>
        <TooltipContent>{t("workbench.stopSession")}</TooltipContent>
      </Tooltip>
      <PreferencesMenu />
    </header>
  );
}

function ConnectionBadge({ status, clockMode }: { status: ConnectionState; clockMode: string }) {
  const { t } = useTranslation();
  const busy = useEvents((s) => s.busy);
  // "Running" alone hid that a command-driven clock stands still between commands (D44).
  const activity = clockMode === "wall" ? "wall" : busy ? "busy" : "idle";
  // No "running" here: the bench is simply open, and the scope panel owns RUN/STOP (issue 31).
  const map: Record<ConnectionState, [string, string]> = {
    connected: [activity === "idle" ? "bg-ok/50" : "bg-ok animate-pulse", t(`workbench.activity.${activity}`)],
    connecting: ["bg-warn animate-pulse", t("connection.connecting")],
    reconnecting: ["bg-error animate-pulse", t("connection.disconnected")],
    unauthorized: ["bg-error", t("connection.unauthorized")],
    gone: ["bg-off", t("errors.notFound")],
  };
  const [dot, label] = map[status];
  const badge = (
    <span className="text-muted-foreground ml-2 inline-flex items-center gap-1.5 text-[11px]" data-testid="session-activity">
      <span className={cn("size-2 rounded-full", dot)} aria-hidden />
      {label}
    </span>
  );
  if (status !== "connected") return badge;
  return (
    <Tooltip>
      <TooltipTrigger asChild>{badge}</TooltipTrigger>
      <TooltipContent className="max-w-xs">{t(`workbench.activityHint.${activity}`)}</TooltipContent>
    </Tooltip>
  );
}
