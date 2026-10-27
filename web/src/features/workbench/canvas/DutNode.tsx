import { type Node, type NodeProps, Position } from "@xyflow/react";
import { CircuitBoard } from "lucide-react";
import { memo } from "react";
import type { ComponentInfo } from "@/api/client";
import { displayUnit, formatEng } from "@/lib/format";
import { cn } from "@/lib/utils";
import { numeric } from "../model";
import { type Issue, IssueMark, PortHandle } from "./InstrumentNode";

export type DutNodeData = { dut: ComponentInfo; editable?: boolean; issue?: Issue };
export type DutNodeType = Node<DutNodeData, "dut">;

const PARAM_UNITS: Record<string, string> = { r: "OHM", c: "F", z_in: "OHM", z_out: "OHM", gain: "" };
const HIDDEN = new Set(["tol_r", "tol_c"]);

function DutNodeImpl({ data, selected }: NodeProps<DutNodeType>) {
  const { dut, editable, issue } = data;
  const ins = dut.ports.filter((p) => p.direction !== "out");
  const outs = dut.ports.filter((p) => p.direction === "out");
  const params = Object.entries(dut.params).filter(([k]) => !HIDDEN.has(k));
  return (
    <div
      className={cn(
        "bg-card text-card-foreground w-[180px] rounded-lg border border-dashed shadow-sm",
        issue === "error" && "border-error",
        issue === "warning" && "border-warn",
        selected && "ring-primary ring-2 shadow-md",
      )}
    >
      <div className="flex items-center gap-2 border-b border-dashed px-3 py-2">
        <CircuitBoard className="text-muted-foreground size-4 shrink-0" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13px] leading-tight font-medium">{dut.label || dut.name}</div>
          <div className="text-muted-foreground num truncate text-[11px] leading-tight">DUT · {dut.type}</div>
        </div>
        {issue && <IssueMark issue={issue} />}
      </div>
      <div className="num grid gap-0.5 px-3 py-1.5 text-[11px]">
        {params.map(([k, v]) => (
          <div key={k} className="flex justify-between gap-2">
            <span className="text-muted-foreground">{k}</span>
            <span>{typeof v === "number" || v === "INF" ? formatEng(numeric(v), displayUnit(PARAM_UNITS[k] ?? ""), 4) : String(v)}</span>
          </div>
        ))}
      </div>
      {ins.map((p, i) => (
        <PortHandle key={p.name} name={p.name} side={Position.Left} index={i} count={ins.length} connected={!!p.net} connectable={editable} />
      ))}
      {outs.map((p, i) => (
        <PortHandle key={p.name} name={p.name} side={Position.Right} index={i} count={outs.length} connected={!!p.net} connectable={editable} />
      ))}
    </div>
  );
}

export const DutNode = memo(DutNodeImpl);
