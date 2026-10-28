import type { InstrumentInternals, InternalComponent } from "@/api/client";

/**
 * The component graph of an instrument (issue 20), laid out in columns along the signal flow:
 * panel ports on the edges, each component one column right of everything that feeds it.
 * Collapsed channels become one group node; their wiring is drawn to the group.
 */

export type PortRow = { name: string; side: "left" | "right" };

export type GraphNode =
  | { id: string; kind: "component"; comp: InternalComponent; rows: PortRow[]; x: number; y: number; height: number }
  | { id: string; kind: "group"; channel: number; count: number; x: number; y: number; height: number }
  | { id: string; kind: "port"; name: string; direction: string; medium: string; channel: number | null; x: number; y: number; height: number };

export type GraphEdge = { id: string; source: string; sourceHandle: string; target: string; targetHandle: string };

export const NODE_WIDTH = { component: 196, group: 196, port: 72 } as const;
export const HEADER = 40;
export const ROW = 18;
const COLUMN = 250;
const GAP = 14;
const BAND_GAP = 28;

export const handleTop = (index: number) => HEADER + 6 + index * ROW + ROW / 2;

export function buildGraph(view: InstrumentInternals, expanded: ReadonlySet<number>): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const comps = new Map(view.components.map((c) => [c.name, c]));
  const visible = (c: InternalComponent) => c.channel == null || expanded.has(c.channel);
  const nodeOf = (comp: string) => {
    const c = comps.get(comp);
    return !c || visible(c) ? `c:${comp}` : `g:${c.channel}`;
  };
  const split = (end: string) => {
    const i = end.lastIndexOf(".");
    return [end.slice(0, i), end.slice(i + 1)] as const;
  };

  // A port without a direction of its own (electrical in/out) sits on the side its panel port faces.
  const towardPanelOut = new Set(view.ports.filter((p) => p.direction === "out").map((p) => p.target));
  const nodes: GraphNode[] = [];
  for (const c of view.components) {
    if (!visible(c)) continue;
    const left: PortRow[] = [];
    const right: PortRow[] = [];
    for (const p of c.ports) {
      const out = p.direction === "out" || (p.direction === "inout" && towardPanelOut.has(`${c.name}.${p.name}`));
      (out ? right : left).push({ name: p.name, side: out ? "right" : "left" });
    }
    const rows = [...left, ...right];
    const height = HEADER + 12 + Math.max(left.length, right.length, 1) * ROW;
    nodes.push({ id: `c:${c.name}`, kind: "component", comp: c, rows, x: 0, y: 0, height });
  }
  const groups = new Map<number, number>();
  for (const c of view.components) if (!visible(c)) groups.set(c.channel!, (groups.get(c.channel!) ?? 0) + 1);
  for (const [channel, count] of groups) nodes.push({ id: `g:${channel}`, kind: "group", channel, count, x: 0, y: 0, height: 56 });
  for (const p of view.ports)
    nodes.push({ id: `p:${p.name}`, kind: "port", name: p.name, direction: p.direction, medium: p.medium, channel: p.channel ?? null, x: 0, y: 0, height: 28 });

  const edges = new Map<string, GraphEdge>();
  const add = (source: string, sourceHandle: string, target: string, targetHandle: string) => {
    if (source === target) return;
    const id = `${source}/${sourceHandle}->${target}/${targetHandle}`;
    edges.set(id, { id, source, sourceHandle, target, targetHandle });
  };
  const handle = (node: string, port: string, side: "in" | "out") => (node.startsWith("g:") ? side : port);
  for (const w of view.wires) {
    const [fc, fp] = split(w.from);
    const [tc, tp] = split(w.to);
    const s = nodeOf(fc);
    const t = nodeOf(tc);
    add(s, handle(s, fp, "out"), t, handle(t, tp, "in"));
  }
  for (const p of view.ports) {
    const [c, port] = split(p.target);
    const n = nodeOf(c);
    if (p.direction === "out") add(n, handle(n, port, "out"), `p:${p.name}`, "p");
    else add(`p:${p.name}`, "p", n, handle(n, port, "in"));
  }

  // Longest path from the sources; the cap keeps a feedback loop from running forever.
  const depth = new Map(nodes.map((n) => [n.id, 0]));
  for (let pass = 0; pass < nodes.length; pass++) {
    let changed = false;
    for (const e of edges.values()) {
      const d = depth.get(e.source)! + 1;
      if (d > depth.get(e.target)!) {
        depth.set(e.target, d);
        changed = true;
      }
    }
    if (!changed) break;
  }
  // Panel outputs sit in the last column, inputs in the first.
  const last = Math.max(0, ...depth.values());
  for (const n of nodes) if (n.kind === "port" && n.direction === "out") depth.set(n.id, last);

  // One band per channel (the whole-instrument parts first), so each channel's chain reads as a row
  // and a collapsed channel lines up with its panel ports.
  const channelOf = (n: GraphNode) => (n.kind === "component" ? n.comp.channel : n.channel) ?? 0;
  const bands = new Map<number, Map<number, GraphNode[]>>();
  for (const n of nodes) {
    const band = bands.get(channelOf(n)) ?? new Map<number, GraphNode[]>();
    const d = depth.get(n.id)!;
    band.set(d, [...(band.get(d) ?? []), n]);
    bands.set(channelOf(n), band);
  }
  const heightOf = (col: GraphNode[]) => col.reduce((h, n) => h + n.height + GAP, -GAP);
  let top = 0;
  for (const channel of [...bands.keys()].sort((a, b) => a - b)) {
    const band = bands.get(channel)!;
    const tallest = Math.max(...[...band.values()].map(heightOf));
    for (const [d, col] of band) {
      col.sort((a, b) => a.id.localeCompare(b.id));
      let y = top + (tallest - heightOf(col)) / 2;
      for (const n of col) {
        n.x = d * COLUMN + (n.kind === "port" && n.direction !== "out" ? NODE_WIDTH.component - NODE_WIDTH.port : 0);
        n.y = y;
        y += n.height + GAP;
      }
    }
    top += tallest + BAND_GAP;
  }
  return { nodes, edges: [...edges.values()] };
}
