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
    ["parts", t("workbench.parts")],
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

export type FieldChange = { path: string; before?: unknown; after?: unknown };
export type DiffItem = {
  /** What the item is: an instrument, a DUT, a wire, or a bench-level section. */
  section: "bench" | "instruments" | "parts" | "wires" | "faults" | "run" | "layout";
  name: string;
  kind: "added" | "removed" | "changed";
  changes: FieldChange[];
};

/** Leaves of a nested object as dotted paths; arrays and scalars are leaves. */
function flatten(v: unknown, prefix = "", out: Record<string, unknown> = {}): Record<string, unknown> {
  if (v && typeof v === "object" && !Array.isArray(v)) {
    const entries = Object.entries(v as Obj);
    if (!entries.length && prefix) out[prefix] = {};
    for (const [k, x] of entries) flatten(x, prefix ? `${prefix}.${k}` : k, out);
  } else if (prefix) {
    out[prefix] = v;
  }
  return out;
}

function fieldChanges(before: unknown, after: unknown): FieldChange[] {
  const a = flatten(before);
  const b = flatten(after);
  const paths = [...new Set([...Object.keys(a), ...Object.keys(b)])];
  return paths.filter((p) => !same(a[p], b[p])).map((p) => ({ path: p, before: a[p], after: b[p] }));
}

const wireName = (w: unknown) => `${String(obj(w).from)} → ${String(obj(w).to)}`;

/**
 * Everything that differs between two versions of a bench file, item by item: which instruments,
 * DUTs and wires were added or removed, and for changed ones, each field with its old and new value.
 */
export function diffItems(before: BenchData, after: BenchData): DiffItem[] {
  const items: DiffItem[] = [];
  const meta = (d: BenchData) => ({ name: d.name, description: d.description, schema: d.schema });
  const metaChanges = fieldChanges(meta(before), meta(after));
  if (metaChanges.length) items.push({ section: "bench", name: "", kind: "changed", changes: metaChanges });

  for (const section of ["instruments", "parts"] as const) {
    const a = obj(before[section]);
    const b = obj(after[section]);
    for (const name of [...new Set([...Object.keys(a), ...Object.keys(b)])]) {
      if (!(name in a)) items.push({ section, name, kind: "added", changes: fieldChanges({}, b[name]) });
      else if (!(name in b)) items.push({ section, name, kind: "removed", changes: fieldChanges(a[name], {}) });
      else if (!same(a[name], b[name])) items.push({ section, name, kind: "changed", changes: fieldChanges(a[name], b[name]) });
    }
  }

  // Wires are matched by their ends; the same ends with other accessories is a changed wire.
  const wa = Array.isArray(before.wires) ? before.wires : [];
  const wb = Array.isArray(after.wires) ? after.wires : [];
  const byName = (ws: unknown[]) => new Map(ws.map((w) => [wireName(w), w]));
  const ma = byName(wa);
  const mb = byName(wb);
  for (const name of [...new Set([...ma.keys(), ...mb.keys()])]) {
    const x = ma.get(name);
    const y = mb.get(name);
    if (x === undefined) items.push({ section: "wires", name, kind: "added", changes: fieldChanges({}, { accessories: obj(y).accessories }) });
    else if (y === undefined) items.push({ section: "wires", name, kind: "removed", changes: [] });
    else if (!same(x, y)) items.push({ section: "wires", name, kind: "changed", changes: fieldChanges(x, y) });
  }

  if (!same(before.faults ?? [], after.faults ?? [])) {
    items.push({ section: "faults", name: "", kind: "changed", changes: [{ path: "faults", before: before.faults, after: after.faults }] });
  }
  const runChanges = fieldChanges(before.run ?? {}, after.run ?? {});
  if (runChanges.length) items.push({ section: "run", name: "", kind: "changed", changes: runChanges });
  const layoutChanges = fieldChanges(before.layout ?? {}, after.layout ?? {});
  if (layoutChanges.length) {
    const moved = [...new Set(layoutChanges.map((c) => c.path.split(".")[0]))];
    items.push({ section: "layout", name: moved.join("、"), kind: "changed", changes: [] });
  }
  return items;
}

/** A field value as the diff view shows it. */
export function showValue(v: unknown): string {
  if (v === undefined) return "—";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
}
