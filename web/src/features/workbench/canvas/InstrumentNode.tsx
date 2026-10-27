import { Handle, type Node, type NodeProps, Position } from "@xyflow/react";
import { Activity, AlertTriangle, AudioWaveform, BarChart3, BatteryCharging, Cpu, Crosshair, Gauge, Radio, SquareFunction, Waves, Zap } from "lucide-react";
import { memo } from "react";
import { useTranslation } from "react-i18next";
import type { InstrumentInfo, StateVarInfo } from "@/api/client";
import { channelColor } from "@/lib/channels";
import { cn } from "@/lib/utils";
import { formatValue, instrumentTitle, stateMap, varName } from "../model";
import { useWorkbench } from "../store";

export type Issue = "error" | "warning";
export type InstrumentNodeData = { inst: InstrumentInfo; editable?: boolean; issue?: Issue; fault?: boolean };
export type InstrumentNodeType = Node<InstrumentNodeData, "instrument">;

export const CLASS_ICON: Record<string, typeof Activity> = {
  oscilloscope: Activity,
  function_generator: AudioWaveform,
  multimeter: Gauge,
  lockin: Crosshair,
  spectrum_analyzer: BarChart3,
  rf_generator: Radio,
  smu: SquareFunction,
  electronic_load: BatteryCharging,
  source: Waves,
};

/**
 * An instrument drawn from its definition alone (D1): the class template picks the summary rows,
 * every other instrument gets the generic layout. No model-specific code.
 */
function InstrumentNodeImpl({ data, selected }: NodeProps<InstrumentNodeType>) {
  const { t } = useTranslation();
  const { inst, editable, issue, fault } = data;
  const Icon = CLASS_ICON[inst.instrument_class] ?? Cpu;
  const vars = stateMap(inst);
  const chanKeys = inst.display.channel_summary.length ? inst.display.channel_summary : [];
  const globalKeys = inst.display.summary;
  const inPorts = inst.ports.filter((p) => p.direction === "in");
  const outPorts = inst.ports.filter((p) => p.direction !== "in");
  const channels = Array.from({ length: inst.channels }, (_, i) => i + 1);
  const isScope = inst.instrument_class === "oscilloscope";
  const visibleChannels = isScope ? channels.filter((n) => vars.get(`ch${n}.display`)?.value) : channels;
  const openScreen = useWorkbench((s) => s.openScreen);

  return (
    <div
      onDoubleClick={() => openScreen(inst.name)}
      className={cn(
        "bg-card text-card-foreground w-[264px] rounded-lg border shadow-sm transition-shadow",
        issue === "error" && "border-error",
        issue === "warning" && "border-warn",
        selected && "ring-primary ring-2 shadow-md",
      )}
    >
      <div className="flex items-center gap-2 border-b px-3 py-2">
        <Icon className="text-muted-foreground size-4 shrink-0" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13px] leading-tight font-medium">{instrumentTitle(inst)}</div>
          <div className="text-muted-foreground num truncate text-[11px] leading-tight">{inst.model}</div>
        </div>
        {issue && <IssueMark issue={issue} />}
        {fault && <FaultMark />}
        {inst.errors.length > 0 && (
          <span
            className="bg-error/15 text-error inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium"
            title={t("workbench.errorsQueued", { count: inst.errors.length })}
          >
            <AlertTriangle className="size-3" />
            <span className="num">{inst.errors.length}</span>
          </span>
        )}
      </div>

      {globalKeys.length > 0 && (
        <div className="text-muted-foreground flex flex-wrap gap-x-3 gap-y-0.5 border-b px-3 py-1.5 text-[11px]">
          {globalKeys.map((k) => {
            const v = vars.get(k);
            return v ? <Summary key={k} v={v} /> : null;
          })}
        </div>
      )}

      <div className="divide-y">
        {visibleChannels.map((n) => (
          <div key={n} className="flex items-center gap-2 px-3 py-1.5">
            <span className="num w-8 shrink-0 text-[11px] font-semibold" style={{ color: channelColor(n) }}>
              CH{n}
            </span>
            <div className="flex min-w-0 flex-1 flex-wrap gap-x-2 gap-y-0.5 text-[11px]">
              {chanKeys
                .filter((k) => k !== "display")
                .map((k) => {
                  const v = vars.get(`ch${n}.${k}`);
                  return v ? <Summary key={k} v={v} channelRow /> : null;
                })}
            </div>
          </div>
        ))}
        {isScope && visibleChannels.length < channels.length && (
          <div className="text-muted-foreground px-3 py-1 text-[11px]">
            {channels
              .filter((n) => !visibleChannels.includes(n))
              .map((n) => `CH${n}`)
              .join(" · ")}{" "}
            · {t("common.off")}
          </div>
        )}
      </div>

      {inPorts.map((p, i) => (
        <PortHandle key={p.name} name={p.name} channel={p.channel} reference={p.medium === "reference"} side={Position.Left} index={i} count={inPorts.length} connected={!!p.net} connectable={editable} />
      ))}
      {outPorts.map((p, i) => (
        <PortHandle key={p.name} name={p.name} channel={p.channel} reference={p.medium === "reference"} side={Position.Right} index={i} count={outPorts.length} connected={!!p.net} connectable={editable} />
      ))}
    </div>
  );
}

function Summary({ v, channelRow }: { v: StateVarInfo; channelRow?: boolean }) {
  const name = varName(v);
  if (v.type === "bool") {
    // Output switches and similar are state words, not just colors (ui-design.md 4.2).
    return (
      <span className={cn("num rounded px-1 font-medium", v.value ? "bg-ok/15 text-ok" : "bg-muted text-muted-foreground")}>
        {name === "output" ? "OUT " : ""}
        {formatValue(v)}
      </span>
    );
  }
  return <span className={cn("num", channelRow ? "text-foreground" : "")}>{formatValue(v)}</span>;
}

export function PortHandle({
  name,
  channel,
  reference,
  side,
  index,
  count,
  connected,
  connectable,
}: {
  name: string;
  channel?: number | null;
  reference?: boolean;
  side: Position;
  index: number;
  count: number;
  connected: boolean;
  connectable?: boolean;
}) {
  const top = `${((index + 1) / (count + 1)) * 100}%`;
  const color = reference ? "var(--medium-reference)" : channelColor(channel);
  return (
    <>
      <Handle
        id={name}
        type="source"
        position={side}
        isConnectable={!!connectable}
        className={cn(connectable && "hover:!scale-150 transition-transform")}
        style={{
          top,
          width: connectable ? 12 : 10,
          height: connectable ? 12 : 10,
          borderWidth: 2,
          borderColor: color,
          background: connected ? color : "var(--card)",
        }}
      />
      <span
        className="num text-muted-foreground pointer-events-none absolute text-[10px]"
        style={{
          top,
          transform: "translateY(-50%)",
          ...(side === Position.Left ? { right: "100%", marginRight: 8 } : { left: "100%", marginLeft: 8 }),
        }}
      >
        {name}
      </span>
    </>
  );
}

/** Marks a node the bench diagnostics point at; the Problems panel says what is wrong. */
export function IssueMark({ issue }: { issue: Issue }) {
  const { t } = useTranslation();
  return (
    <span
      className={cn(
        "inline-flex size-5 shrink-0 items-center justify-center rounded",
        issue === "error" ? "bg-error/15 text-error" : "bg-warn/15 text-warn",
      )}
      title={t("panel.problems")}
      aria-label={t("panel.problems")}
    >
      <AlertTriangle className="size-3" />
    </span>
  );
}

/** An injected fault is active on this instrument or DUT. */
export function FaultMark() {
  const { t } = useTranslation();
  return (
    <span
      className="bg-error text-primary-foreground inline-flex size-5 shrink-0 items-center justify-center rounded"
      title={t("fault.title")}
      aria-label={t("fault.title")}
    >
      <Zap className="size-3" />
    </span>
  );
}

export const InstrumentNode = memo(InstrumentNodeImpl);
