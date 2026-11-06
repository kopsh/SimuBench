import { Crosshair, Info, Play, Square, StepForward } from "lucide-react";
import { useDeferredValue, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import type { InstrumentInfo, ScopeScreen as ScopeScreenData, SessionInfo, Trace } from "@/api/client";
import { useEvents } from "@/api/events";
import { usePanel, useScopeScreen } from "@/api/hooks";
import { remoteInstrument } from "@/components/app/ClientsConfirm";
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
      onError: (e) => !remoteInstrument(e) && toast.error((e as Error).message), // the hook says why, with LOCAL
    });
  const running = !!data?.running;
  const hint = running ? t("scope.liveHint") : t("scope.recordHint");
  const [cursors, setCursors] = useState<Cursors | null>(null);
  const channelScale = (n: number) => Number(vars.get(`ch${n}.scale`)?.value ?? 1);

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
        <Button
          size="sm"
          variant={cursors ? "default" : "outline"}
          className="h-7 gap-1.5 text-xs"
          aria-pressed={!!cursors}
          onClick={() => setCursors(cursors ? null : DEFAULT_CURSORS)}
        >
          <Crosshair className="size-3.5" />
          {t("scope.cursors.toggle")}
        </Button>
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
            {/* A running screen draws live frames, not acquisitions (#280): say so rather than "0". */}
            {data.source === "live" ? (
              <span data-testid="scope-source">{t("scope.liveFrame")}</span>
            ) : (
              <span data-testid="scope-source">
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
            {cursors && data && (
              <CursorOverlay
                cursors={cursors}
                onChange={setCursors}
                timePerDiv={data.timebase_scale}
                voltsPerDiv={channelScale(cursors.channel)}
              />
            )}
          </>
        )}
      </div>

      {cursors && data && (
        <CursorBar
          cursors={cursors}
          onChange={setCursors}
          timePerDiv={data.timebase_scale}
          voltsPerDiv={channelScale(cursors.channel)}
          channels={inst.channels}
        />
      )}
      {data && data.measurements.length > 0 && <MeasurementTable data={data} />}

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
                    {t(`scope.silence.${r.kind}`, { source, fault: r.fault_id ?? "" })}
                  </button>
                ) : (
                  t(`scope.silence.${r.kind}`, { source, fault: r.fault_id ?? "" })
                )}
              </span>
            </div>
          );
        }),
      )}
    </div>
  );
}

/** Two time cursors (divisions from the left edge) and two voltage cursors (divisions from the
 * center) on one channel's scale; the screen's graticule is 10 × 8 divisions (#280). */
type Cursors = { x1: number; x2: number; y1: number; y2: number; channel: number };
const DEFAULT_CURSORS: Cursors = { x1: 2, x2: 8, y1: 2, y2: -2, channel: 1 };

type CursorKey = "x1" | "x2" | "y1" | "y2";

/** The cursors drawn on the screen and moved there, as on the instrument (#356): drag a line;
 * the readout sits in the screen's corner. The sliders below stay as the keyboard path. */
function CursorOverlay({
  cursors,
  onChange,
  timePerDiv,
  voltsPerDiv,
}: {
  cursors: Cursors;
  onChange: (c: Cursors) => void;
  timePerDiv: number;
  voltsPerDiv: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [dragging, setDragging] = useState<CursorKey | null>(null);
  const color = channelColor(cursors.channel);
  const round = (v: number) => Math.round(v * 100) / 100;
  const move = (key: CursorKey, e: React.PointerEvent) => {
    const box = ref.current?.getBoundingClientRect();
    if (!box || box.width === 0 || box.height === 0) return;
    const value = key.startsWith("x")
      ? Math.min(10, Math.max(0, ((e.clientX - box.left) / box.width) * 10))
      : Math.min(4, Math.max(-4, 4 - ((e.clientY - box.top) / box.height) * 8));
    onChange({ ...cursors, [key]: round(value) });
  };
  const handle = (key: CursorKey) => {
    const vertical = key.startsWith("x");
    const pos = vertical ? `${(cursors[key] / 10) * 100}%` : `${((4 - cursors[key]) / 8) * 100}%`;
    return (
      <div
        key={key}
        data-testid={`cursor-${key}`}
        className={cn(
          "pointer-events-auto absolute touch-none",
          vertical ? "top-0 bottom-0 -ml-1.5 w-3 cursor-ew-resize" : "right-0 left-0 -mt-1.5 h-3 cursor-ns-resize",
        )}
        style={vertical ? { left: pos } : { top: pos }}
        onPointerDown={(e) => {
          e.currentTarget.setPointerCapture(e.pointerId);
          setDragging(key);
        }}
        onPointerMove={(e) => dragging === key && move(key, e)}
        onPointerUp={(e) => {
          e.currentTarget.releasePointerCapture(e.pointerId);
          setDragging(null);
        }}
      >
        <div
          className={cn(
            "absolute border-dashed",
            vertical ? "top-0 bottom-0 left-1.5 border-l border-white/70" : "top-1.5 right-0 left-0 border-t",
            dragging === key && "border-solid",
          )}
          style={vertical ? undefined : { borderColor: color }}
        />
      </div>
    );
  };
  const dt = (cursors.x2 - cursors.x1) * timePerDiv;
  const dv = (cursors.y1 - cursors.y2) * voltsPerDiv;
  return (
    <div ref={ref} className="pointer-events-none absolute inset-0" data-testid="scope-cursors">
      {(["x1", "x2", "y1", "y2"] as const).map(handle)}
      <div
        className="bg-card/85 num absolute top-2 right-2 rounded border px-2 py-1 text-[11px] leading-tight"
        data-testid="cursor-readout"
      >
        <div>Δt {formatEng(dt, "s", 4)}</div>
        <div>1/Δt {dt !== 0 ? formatEng(1 / Math.abs(dt), "Hz", 4) : "—"}</div>
        <div style={{ color }}>ΔV {formatEng(dv, "V", 4)}</div>
      </div>
    </div>
  );
}

function CursorBar({
  cursors,
  onChange,
  timePerDiv,
  voltsPerDiv,
  channels,
}: {
  cursors: Cursors;
  onChange: (c: Cursors) => void;
  timePerDiv: number;
  voltsPerDiv: number;
  channels: number;
}) {
  const { t } = useTranslation();
  const dt = (cursors.x2 - cursors.x1) * timePerDiv;
  const dv = (cursors.y1 - cursors.y2) * voltsPerDiv;
  const slider = (key: "x1" | "x2" | "y1" | "y2", min: number, max: number) => (
    <label className="flex items-center gap-1.5">
      <span className="text-muted-foreground">{t(`scope.cursors.${key}`)}</span>
      <input
        type="range"
        min={min}
        max={max}
        step={0.1}
        value={cursors[key]}
        aria-label={t(`scope.cursors.${key}`)}
        onChange={(e) => onChange({ ...cursors, [key]: Number(e.target.value) })}
        className="w-24"
      />
    </label>
  );
  return (
    <div className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-1 rounded-md border px-2.5 py-1.5 text-xs" role="group" aria-label={t("scope.cursors.title")}>
      {slider("x1", 0, 10)}
      {slider("x2", 0, 10)}
      {slider("y1", -4, 4)}
      {slider("y2", -4, 4)}
      <label className="flex items-center gap-1.5">
        <span className="text-muted-foreground">{t("scope.cursors.channel")}</span>
        <select
          className="bg-background rounded border px-1"
          value={cursors.channel}
          onChange={(e) => onChange({ ...cursors, channel: Number(e.target.value) })}
        >
          {Array.from({ length: channels }, (_, i) => i + 1).map((n) => (
            <option key={n} value={n}>
              CH{n}
            </option>
          ))}
        </select>
      </label>
      <span className="ml-auto flex gap-4">
        <span>
          Δt <span className="num" data-testid="cursor-dt">{formatEng(dt, "s", 4)}</span>
        </span>
        <span>
          1/Δt <span className="num" data-testid="cursor-inv-dt">{dt !== 0 ? formatEng(1 / Math.abs(dt), "Hz", 4) : "—"}</span>
        </span>
        <span>
          ΔV <span className="num" data-testid="cursor-dv">{formatEng(dv, "V", 4)}</span>
        </span>
      </span>
    </div>
  );
}

/** Per displayed channel, the scope's own automatic measurements of the record on screen (#280):
 * the same rules as its :MEASure? replies. */
function MeasurementTable({ data }: { data: ScopeScreenData }) {
  const { t } = useTranslation();
  const cell = (v: number | null | undefined, unit: string) => (v == null ? "—" : formatEng(v, unit, 4));
  const columns: { key: "vpp" | "frequency" | "period" | "mean" | "rms"; unit: string }[] = [
    { key: "vpp", unit: "V" },
    { key: "frequency", unit: "Hz" },
    { key: "period", unit: "s" },
    { key: "mean", unit: "V" },
    { key: "rms", unit: "V" },
  ];
  return (
    <table className="shrink-0 text-xs" aria-label={t("scope.measure.title")}>
      <thead>
        <tr className="text-muted-foreground">
          <th className="pr-4 text-left font-normal">{t("scope.measure.title")}</th>
          {columns.map((c) => (
            <th key={c.key} className="pr-4 text-right font-normal">
              {t(`scope.measure.${c.key}`)}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {data.measurements.map((m) => (
          <tr key={m.channel}>
            <td className="num pr-4 font-semibold" style={{ color: channelColor(m.channel) }}>
              CH{m.channel}
            </td>
            {columns.map((c) => (
              <td key={c.key} className="num pr-4 text-right" data-testid={`meas-ch${m.channel}-${c.key}`}>
                {cell(m[c.key], c.unit)}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
