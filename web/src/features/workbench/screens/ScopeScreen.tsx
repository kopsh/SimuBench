import { Info } from "lucide-react";
import { useDeferredValue, useState } from "react";
import { useTranslation } from "react-i18next";
import type { InstrumentInfo } from "@/api/client";
import { useEvents } from "@/api/events";
import { useScopeScreen } from "@/api/hooks";
import { EmptyState } from "@/components/app/StateViews";
import { Skeleton } from "@/components/ui/skeleton";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { channelColor } from "@/lib/channels";
import { formatEng } from "@/lib/format";
import { cn } from "@/lib/utils";
import { stateMap } from "../model";
import { WaveformCanvas } from "./WaveformCanvas";

export function ScopeScreen({ sid, inst }: { sid: string; inst: InstrumentInfo }) {
  const { t } = useTranslation();
  const [source, setSource] = useState<"preview" | "record">("preview");
  const version = useDeferredValue(useEvents((s) => s.version));
  const screen = useScopeScreen(sid, inst.name, source, version);
  const vars = stateMap(inst);
  const data = screen.data;

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
          <WaveformCanvas
            className="h-full w-full rounded-md"
            traces={data?.traces ?? []}
            triggerLevel={data?.trigger_level}
            triggerChannel={data?.trigger_source}
          />
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
