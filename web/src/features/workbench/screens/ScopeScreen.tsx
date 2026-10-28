import { Info, Play, Square, StepForward } from "lucide-react";
import { useDeferredValue, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import type { InstrumentInfo } from "@/api/client";
import { useEvents } from "@/api/events";
import { useScopeScreen, useScpi } from "@/api/hooks";
import { EmptyState } from "@/components/app/StateViews";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { channelColor } from "@/lib/channels";
import { formatEng } from "@/lib/format";
import { cn } from "@/lib/utils";
import { stateMap } from "../model";
import { WaveformCanvas } from "./WaveformCanvas";

/** Pause between acquisitions while running, in real time. */
const RUN_INTERVAL_MS = 300;

export function ScopeScreen({ sid, inst, readonly }: { sid: string; inst: InstrumentInfo; readonly: boolean }) {
  const { t } = useTranslation();
  const [source, setSource] = useState<"preview" | "record">("preview");
  const version = useDeferredValue(useEvents((s) => s.version));
  const screen = useScopeScreen(sid, inst.name, source, version);
  const vars = stateMap(inst);
  const data = screen.data;
  const acquire = useAcquire(sid, inst.name, readonly);
  const single = () => {
    setSource("record");
    acquire.single();
  };
  const run = () => {
    setSource("record");
    acquire.setRunning(!acquire.running);
  };

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-3">
      <div className="flex items-center gap-3">
        <ToggleGroup
          type="single"
          size="sm"
          variant="outline"
          value={source}
          onValueChange={(v) => v && setSource(v as typeof source)}
          aria-label={t("scope.title")}
        >
          <ToggleGroupItem value="preview" className="px-3 text-xs">
            {t("scope.preview")}
          </ToggleGroupItem>
          <ToggleGroupItem value="record" className="px-3 text-xs">
            {t("scope.record")}
          </ToggleGroupItem>
        </ToggleGroup>
        <div role="group" aria-label={t("scope.acquisition")} className="flex items-center gap-1">
          <Button size="sm" variant={acquire.running ? "default" : "outline"} className="h-7 gap-1.5 text-xs" disabled={readonly} onClick={run}>
            {acquire.running ? <Square className="size-3.5" /> : <Play className="size-3.5" />}
            {acquire.running ? t("scope.stop") : t("scope.run")}
          </Button>
          <Button size="sm" variant="outline" className="h-7 gap-1.5 text-xs" disabled={readonly || acquire.running || acquire.busy} onClick={single}>
            <StepForward className="size-3.5" />
            {t("scope.single")}
          </Button>
        </div>
        <Tooltip>
          <TooltipTrigger asChild>
            <Info className="text-muted-foreground size-4" aria-label={source === "preview" ? t("scope.previewHint") : t("scope.recordHint")} />
          </TooltipTrigger>
          <TooltipContent className="max-w-xs">{source === "preview" ? t("scope.previewHint") : t("scope.recordHint")}</TooltipContent>
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
            {source === "record" && (
              <span>
                {t("scope.acquisitions")} <span className="num text-foreground">{data.acquisitions}</span>
              </span>
            )}
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
        ) : data && data.traces.length === 0 && source === "record" ? (
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
            {data && data.traces.length > 0 && data.traces.every((tr) => tr.volts.every((v) => v === 0)) && (
              <div className="bg-card/90 text-muted-foreground absolute top-3 left-1/2 -translate-x-1/2 rounded-md border px-3 py-1.5 text-xs">
                {t("scope.noSignal")}
              </div>
            )}
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
 * Front-panel acquisition: Single sends :SINGle once, Run repeats it until stopped. The commands go
 * to the instrument like a script's, so they show in the communication log and advance virtual time.
 */
function useAcquire(sid: string, name: string, readonly: boolean) {
  const scpi = useScpi(sid, name);
  const [running, setRunning] = useState(false);
  const send = scpi.mutateAsync; // stable across renders (TanStack Query)
  useEffect(() => {
    if (!running || readonly) return;
    let stop = false;
    void (async () => {
      while (!stop) {
        try {
          const r = await send(":SINGle");
          if (r.link_error || r.errors.length) break; // a fault or a refused command stops the run
        } catch {
          break;
        }
        await new Promise((done) => setTimeout(done, RUN_INTERVAL_MS));
      }
      if (!stop) setRunning(false);
    })();
    return () => {
      stop = true;
    };
  }, [running, readonly, send]);
  return { running: running && !readonly, setRunning, single: () => scpi.mutate(":SINGle"), busy: scpi.isPending };
}
