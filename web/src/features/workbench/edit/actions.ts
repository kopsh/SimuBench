import type { BenchData, Catalog } from "@/api/client";
import { centerOfView, type LibraryItem } from "../canvas/Canvas";
import { useWorkbench } from "../store";
import { applyOps, useEditor } from "./editor";

export const NAME = /^[A-Za-z_][A-Za-z0-9_-]{0,47}$/;

const CLASS_PREFIX: Record<string, string> = {
  oscilloscope: "scope",
  function_generator: "awg",
  spectrum_analyzer: "sa",
  multimeter: "dmm",
  smu: "smu",
  lockin: "lockin",
  rf_generator: "rf",
  electronic_load: "eload",
};
const TYPE_PREFIX: Record<string, string> = { rc_lowpass: "rc", resistor: "r", load_resistor: "r", unity_buffer: "buf", dc_supply: "supply" };

function names(data: BenchData | undefined): Set<string> {
  const keys = (v: unknown) => Object.keys(v && typeof v === "object" ? (v as object) : {});
  return new Set([...keys(data?.instruments), ...keys(data?.duts)]);
}

/** A short, unused instance name: scope, scope2, scope3, … */
export function suggestName(base: string, taken: Set<string>): string {
  const clean = base.replace(/[^A-Za-z0-9_-]/g, "_").replace(/^([^A-Za-z_])/, "_$1") || "item";
  if (!taken.has(clean)) return clean;
  for (let n = 2; ; n++) if (!taken.has(`${clean}${n}`)) return `${clean}${n}`;
}

/** Add a library item to the bench file and select it. */
export async function addItem(catalog: Catalog, item: LibraryItem, at?: { x: number; y: number }) {
  const taken = names(useEditor.getState().doc?.data);
  // Not dropped: the middle of the view, nudged so that repeated adds do not stack exactly.
  const center = at ?? centerOfView();
  const nudge = at ? 0 : (taken.size % 5) * 24;
  const pos = center ? { x: Math.round(center.x - 120 + nudge), y: Math.round(center.y - 60 + nudge) } : {};
  let name: string;
  if (item.kind === "instrument") {
    const def = catalog.instruments.find((d) => d.id === item.id);
    name = suggestName(CLASS_PREFIX[def?.instrument_class ?? ""] ?? item.id.split("-")[1] ?? "inst", taken);
    const done = await applyOps([{ op: "add_instrument", name, model: item.id, ...pos }]);
    if (done) useWorkbench.getState().select({ kind: "instrument", id: name });
  } else {
    name = suggestName(TYPE_PREFIX[item.id] ?? item.id, taken);
    const done = await applyOps([{ op: "add_dut", name, type: item.id, ...pos }]);
    if (done) useWorkbench.getState().select({ kind: "dut", id: name });
  }
  return name;
}
