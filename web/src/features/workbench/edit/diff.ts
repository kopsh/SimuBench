import type { BenchData } from "@/api/client";
import i18n from "@/i18n";

type Obj = Record<string, unknown>;

const obj = (v: unknown): Obj => (v && typeof v === "object" && !Array.isArray(v) ? (v as Obj) : {});
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

/** A one-line summary of what changed between two versions of a bench file. */
export function describeChange(before: BenchData, after: BenchData): string {
  const t = i18n.t.bind(i18n);
  const parts: string[] = [];
  for (const [key, label] of [
    ["instruments", t("workbench.instruments")],
    ["duts", t("workbench.duts")],
  ] as const) {
    const a = obj(before[key]);
    const b = obj(after[key]);
    const added = Object.keys(b).filter((k) => !(k in a));
    const removed = Object.keys(a).filter((k) => !(k in b));
    const changed = Object.keys(b).filter((k) => k in a && !same(a[k], b[k]));
    if (added.length) parts.push(`+${added.length} ${label}（${added.join("、")}）`);
    if (removed.length) parts.push(`−${removed.length} ${label}（${removed.join("、")}）`);
    if (changed.length) parts.push(t("edit.changedItems", { names: changed.join("、") }));
  }
  const wa = Array.isArray(before.wires) ? before.wires : [];
  const wb = Array.isArray(after.wires) ? after.wires : [];
  const key = (w: unknown) => JSON.stringify(w);
  const setA = new Set(wa.map(key));
  const setB = new Set(wb.map(key));
  const wAdded = wb.filter((w) => !setA.has(key(w))).length;
  const wRemoved = wa.filter((w) => !setB.has(key(w))).length;
  if (wAdded) parts.push(`+${wAdded} ${t("workbench.wires")}`);
  if (wRemoved) parts.push(`−${wRemoved} ${t("workbench.wires")}`);
  if (!same(before.run, after.run)) parts.push(t("edit.runChanged"));
  if (!parts.length && !same(before.layout, after.layout)) parts.push(t("edit.layoutChanged"));
  return parts.join(" · ");
}
