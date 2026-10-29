import { Info, Play, Square, StepForward } from "lucide-react";
import { useDeferredValue } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import type { InstrumentInfo, SessionInfo, Trace } from "@/api/client";
import { useEvents } from "@/api/events";
import { usePanel, useScopeScreen } from "@/api/hooks";
import { EmptyState } from "@/components/app/StateViews";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { channelColor } from "@/lib/channels";
import { formatEng } from "@/lib/format";
import { cn } from "@/lib/utils";
import { refTarget, stateMap } from "../model";
import { useWorkbench } from "../store";
import { WaveformCanvas } from "./WaveformCanvas";

export function ScopeScreen({ session, inst, readonly }: { session: SessionInfo; inst: InstrumentInfo; readonly: boolean }) {
  const { t } = useTranslation();
  const sid = session.id;
  const version = useDeferredValue(useEvents((s) => s.version));
  const screen = useScopeScreen(sid, inst.name, version);
  const vars = stateMap(inst);
  const data = screen.data;
  // Front-panel buttons act inside the instrument, like the real one's: no SCPI, no virtual time (D32).
  const panel = usePanel(sid, inst.name);
  const press = (action: "run" | "stop" | "single") =>
    panel.mutate(action, {
      onSuccess: (r) => {
        if (!r.ok && r.error) toast.error(`${r.error.code} ${r.error.message}`);
      },
      onError: (e) => toast.error((e as Error).message),
    });
  const running = !!data?.running;
  const hint = running ? t("scope.liveHint") : t("scope.recordHint");

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-3">
      <div className="flex items-center gap-3">
        <div role="group" aria-label={t("scope.acquisition")} className="flex items-center gap-1">
          <Button
            size="sm"
            variant={running ? "default" : "outline"}
            className="h-7 gap-1.5 text-xs"
            disabled={readonly || !data || panel.isPending}
            onClick={() => press(running ? "stop" : "run")}
          >
            {running ? <Square className="size-3.5" /> : <Play className="size-3.5" />}
            {running ? t("scope.stop") : t("scope.run")}
          </Button>
          <Button size="sm" variant="outline" className="h-7 gap-1.5 text-xs" disabled={readonly || panel.isPending} onClick={() => press("single")}>
            <StepForward className="size-3.5" />
            {t("scope.single")}
          </Button>
        </div>
        {data && (
          <span className={cn("text-xs", running ? "text-ok" : data.waiting ? "text-warn" : "text-muted-foreground")}>
            {running ? t("scope.running") : data.waiting ? t("scope.waiting") : t("scope.stopped")}
          </span>
        )}
        <Tooltip>
          <TooltipTrigger asChild>
            <Info className="text-muted-foreground size-4" aria-label={hint} />
          </TooltipTrigger>
          <TooltipContent className="max-w-xs">{hint}</TooltipContent>
        </Tooltip>
        {data && (
          <div className="text-muted-foreground ml-auto flex items-center gap-4 text-xs">
            <span>
              {t("scope.timebase")} <span className="num text-foreground">{formatEng(data.timebase_scale, "s", 3)}</span>
              {t("scope.perDiv")}
            </span>
            <span>
              {t("scope.trigger")}{" "}
              <span className="num" style={{ color: channelColor(data.trigger_source) }}>
                CH{data.trigger_source}
              </span>{" "}
              <span className="num text-foreground">{formatEng(data.trigger_level, "V", 3)}</span>
            </span>
            <span>
              {t("scope.acquisitions")} <span className="num text-foreground">{data.acquisitions}</span>
            </span>
            {data.traces[0] && (
              <span
                className={cn(
                  "rounded px-1.5 py-0.5 text-[11px] font-medium",
                  data.traces[0].triggered ? "bg-ok/15 text-ok" : "bg-warn/15 text-warn",
                )}
              >
                {data.traces[0].triggered ? t("scope.triggered") : t("scope.autoTriggered")}
              </span>
            )}
          </div>
        )}
      </div>

      <div className="relative min-h-0 flex-1">
        {screen.isLoading ? (
          <Skeleton className="h-full w-full" />
        ) : data && data.traces.length === 0 && !running ? (
          <div className="bg-plot flex h-full items-center justify-center rounded-md">
            <EmptyState title={t("scope.noRecord")} />
          </div>
        ) : (
          <>
            <WaveformCanvas
              className="h-full w-full rounded-md"
              traces={data?.traces ?? []}
              triggerLevel={data?.trigger_level}
              triggerChannel={data?.trigger_source}
            />
            {data && <SilenceNotice session={session} traces={data.traces} />}
          </>
        )}
      </div>

      <div className="grid shrink-0 grid-cols-4 gap-2">
        {Array.from({ length: inst.channels }, (_, i) => i + 1).map((n) => {
          const on = !!vars.get(`ch${n}.display`)?.value;
          const scale = Number(vars.get(`ch${n}.scale`)?.value ?? 1);
          const coupling = String(vars.get(`ch${n}.coupling`)?.value ?? "");
          const probe = Number(vars.get(`ch${n}.probe`)?.value ?? 1);
          return (
            <div key={n} className={cn("rounded-md border px-2.5 py-1.5 text-xs", !on && "opacity-50")}>
              <div className="num font-semibold" style={{ color: channelColor(n) }}>
                CH{n}
              </div>
              <div className="num">
                {formatEng(scale, "V", 3)}
                {t("scope.perDiv")} · {coupling} · {probe}×
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/**
 * Why a channel reads 0 V, traced upstream by the service (unwired, source off, set to 0, no source,
 * a fault), with each hop selectable so it can be found on the canvas and in the inspector.
 */
function SilenceNotice({ session, traces }: { session: SessionInfo; traces: Trace[] }) {
  const { t } = useTranslation();
  const select = useWorkbench((s) => s.select);
  const silent = traces.filter((tr) => tr.silence.length > 0 && tr.volts.every((v) => v === 0));
  if (silent.length === 0) return null;
  return (
    <div
      role="status"
      aria-label={t("scope.silence.title")}
      className="bg-card/90 absolute top-3 left-1/2 w-max max-w-[90%] -translate-x-1/2 space-y-1 rounded-md border px-3 py-1.5 text-xs"
    >
      {silent.map((tr) =>
        tr.silence.map((r, i) => {
          const source = r.source ? refTarget(session, r.source).label : "";
          return (
            <div key={`${tr.channel}-${i}`} className="text-muted-foreground flex flex-wrap items-center gap-1">
              <span className="num font-semibold" style={{ color: channelColor(tr.channel) }}>
                CH{tr.channel}
              </span>
              {r.path.slice(1, r.source ? -1 : undefined).map((ref) => {
                const hop = refTarget(session, ref);
                return (
                  <span key={ref} className="flex items-center gap-1">
                    <span aria-hidden>←</span>
                    <button className="text-foreground hover:underline" disabled={!hop.selection} onClick={() => hop.selection && select(hop.selection)}>
                      {hop.label}
                    </button>
                  </span>
                );
              })}
              {r.source && <span aria-hidden>←</span>}
              <span>
                {r.source && r.kind !== "fault" ? (
                  <button
                    className="text-foreground hover:underline"
                    onClick={() => {
                      const sel = refTarget(session, r.source!).selection;
                      if (sel) select(sel);
                    }}
                  >
                    {t(`scope.silence.${r.kind}`, { source, fault: r.fault_id })}
                  </button>
                ) : (
                  t(`scope.silence.${r.kind}`, { source, fault: r.fault_id })
                )}
              </span>
            </div>
          );
        }),
      )}
    </div>
  );
}
