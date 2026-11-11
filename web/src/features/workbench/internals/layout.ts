import type { InstrumentInternals, InternalComponent, InternalWire } from "@/api/client";
import { probeable } from "./probe";

/**
 * The component graph of an instrument (issue 20), laid out in columns along the signal flow
 * (signal-chain.md 1.2): panel ports on the edges, each component one column right of every
 * component whose signal it takes. Only the signal wires decide where a wired component stands; a
 * dependency (its ``refs``, #729) never pushes it. A component reached by dependencies alone stands
 * beside what it connects to: one that others use one column before its first user (the time base,
 * the trigger, a local oscillator), one that only uses others one column after the last of them (the
 * measurements, the transfer). Components nothing connects, only commands and settings drive (the
 * system, the status registers), wait in a band of their own. Collapsed channels become one group
 * node; their wiring is drawn to the group.
 */

/** A port's row on its component; ``probe``: an output the internal probe reads (signal-chain.md 1.3). */
export type PortRow = { name: string; side: "left" | "right"; probe: boolean };

export type GraphNode =
  | { id: string; kind: "component"; comp: InternalComponent; rows: PortRow[]; x: number; y: number; height: number }
  | { id: string; kind: "group"; channel: number; count: number; x: number; y: number; height: number }
  | { id: string; kind: "port"; name: string; direction: string; medium: string; channel: number | null; x: number; y: number; height: number }
  | { id: string; kind: "label"; text: "command"; x: number; y: number; height: number };

/**
 * A signal wire, or a dependency (``ref``: the source is used by the target, ``label`` names the
 * parameter). ``links`` are the component-level ``a>b`` it draws (a panel port is ``p:<name>``) — several
 * when a collapsed channel draws them to its group — so a trace (#739) can light it.
 */
export type GraphEdge = {
  id: string;
  source: string;
  sourceHandle: string;
  target: string;
  targetHandle: string;
  kind: "wire" | "ref";
  links: string[];
  label?: string;
  /** A signal wire inside the instrument: what flows on it (analog, or a sample stream with its rate and format). */
  flow?: Pick<InternalWire, "medium" | "sample_rate_hz" | "format">;
};

export const NODE_WIDTH = { component: 196, group: 196, port: 72, label: 196 } as const;
export const HEADER = 40;
/** Handles for a dependency: on the header, apart from the ports' rows. */
export const REF_HANDLE_TOP = HEADER / 2;
export const ROW = 18;
/** Room between columns for a wire's label (`数字 · 2 GSa/s · 8 位`, signal-chain.md 1.2). */
const COLUMN = 330;
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
      (out ? right : left).push({ name: p.name, side: out ? "right" : "left", probe: probeable(p) });
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
  const add = (
    source: string,
    sourceHandle: string,
    target: string,
    targetHandle: string,
    link: string,
    extra: { ref?: string; flow?: GraphEdge["flow"] } = {},
  ) => {
    if (source === target) return;
    const { ref, flow } = extra;
    const id = `${source}/${sourceHandle}->${target}/${targetHandle}${ref ? `:${ref}` : ""}`;
    const seen = edges.get(id);
    if (seen) seen.links.push(link);
    else
      edges.set(id, {
        id,
        source,
        sourceHandle,
        target,
        targetHandle,
        kind: ref ? "ref" : "wire",
        links: [link],
        ...(ref ? { label: ref } : {}),
        ...(flow ? { flow } : {}),
      });
  };
  const handle = (node: string, port: string, side: "in" | "out") => (node.startsWith("g:") ? side : port);
  for (const w of view.wires) {
    const [fc, fp] = split(w.from);
    const [tc, tp] = split(w.to);
    const s = nodeOf(fc);
    const t = nodeOf(tc);
    const flow = { medium: w.medium ?? "signal", sample_rate_hz: w.sample_rate_hz ?? null, format: w.format ?? null };
    add(s, handle(s, fp, "out"), t, handle(t, tp, "in"), `${fc}>${tc}`, { flow });
  }
  for (const p of view.ports) {
    const [c, port] = split(p.target);
    const n = nodeOf(c);
    if (p.direction === "out") add(n, handle(n, port, "out"), `p:${p.name}`, "p", `${c}>p:${p.name}`);
    else add(`p:${p.name}`, "p", n, handle(n, port, "in"), `p:${p.name}>${c}`);
  }
  // What a component uses feeds it: the used one is the source, so it stands left of its user.
  const refHandle = (node: string, side: "in" | "out") => (node.startsWith("g:") ? side : `ref-${side}`);
  for (const r of view.refs ?? []) {
    const used = nodeOf(r.from);
    const user = nodeOf(r.to);
    add(used, refHandle(used, "out"), user, refHandle(user, "in"), `${r.from}>${r.to}`, { ref: r.param });
  }

  // Components nothing connects and no panel port leads to are driven by commands and settings alone:
  // a band of their own at the bottom, one column each, under a label.
  const touched = new Set<string>();
  for (const e of edges.values()) {
    touched.add(e.source);
    touched.add(e.target);
  }
  const commandSide = nodes.filter((n) => n.kind === "component" && !touched.has(n.id));
  const COMMAND_BAND = 1000;

  // The signal sets the columns: the longest path along the wires (and panel ports) alone; the
  // cap keeps a feedback loop from running forever.
  const all = [...edges.values()];
  const wires = all.filter((e) => e.kind === "wire");
  const refs = all.filter((e) => e.kind === "ref");
  const depth = new Map(nodes.map((n) => [n.id, 0]));
  for (let pass = 0; pass < nodes.length; pass++) {
    let changed = false;
    for (const e of wires) {
      const d = depth.get(e.source)! + 1;
      if (d > depth.get(e.target)!) {
        depth.set(e.target, d);
        changed = true;
      }
    }
    if (!changed) break;
  }
  // Off the signal path: a block others use stands one column before its first user, one that only
  // uses others one column after the last of them. Settled in a few passes (a chain of them).
  const onWire = new Set(wires.flatMap((e) => [e.source, e.target]));
  const offPath = nodes.filter((n) => !onWire.has(n.id) && refs.some((e) => e.source === n.id || e.target === n.id));
  offPath.sort((a, b) => a.id.localeCompare(b.id));
  for (let pass = 0; pass < nodes.length; pass++) {
    let changed = false;
    for (const n of offPath) {
      const users = refs.filter((e) => e.source === n.id).map((e) => depth.get(e.target)!);
      const used = refs.filter((e) => e.target === n.id).map((e) => depth.get(e.source)!);
      const d = users.length ? Math.max(0, Math.min(...users) - 1) : Math.max(...used) + 1;
      if (d !== depth.get(n.id)) {
        depth.set(n.id, d);
        changed = true;
      }
    }
    if (!changed) break;
  }
  // Panel outputs sit in the last column, inputs in the first.
  const last = Math.max(0, ...depth.values());
  for (const n of nodes) if (n.kind === "port" && n.direction === "out") depth.set(n.id, last);

  if (commandSide.length) {
    nodes.push({ id: "l:command", kind: "label", text: "command", x: 0, y: 0, height: 40 });
    depth.set("l:command", 0);
    commandSide.sort((a, b) => a.id.localeCompare(b.id)).forEach((n, i) => depth.set(n.id, i + 1));
  }
  const isCommandSide = new Set([...commandSide.map((n) => n.id), "l:command"]);

  // One band per channel (the whole-instrument parts first), so each channel's chain reads as a row
  // and a collapsed channel lines up with its panel ports; the command side is the last band.
  const channelOf = (n: GraphNode) => {
    if (isCommandSide.has(n.id)) return COMMAND_BAND;
    return (n.kind === "component" ? n.comp.channel : n.kind === "label" ? null : n.channel) ?? 0;
  };
  const bands = new Map<number, Map<number, GraphNode[]>>();
  for (const n of nodes) {
    const band = bands.get(channelOf(n)) ?? new Map<number, GraphNode[]>();
    const d = depth.get(n.id)!;
    band.set(d, [...(band.get(d) ?? []), n]);
    bands.set(channelOf(n), band);
  }
  const heightOf = (col: GraphNode[]) => col.reduce((h, n) => h + n.height + GAP, -GAP);
  const tallestOf = (band: Map<number, GraphNode[]>) => Math.max(...[...band.values()].map(heightOf));
  const place = (band: Map<number, GraphNode[]>, top: number) => {
    const tallest = tallestOf(band);
    for (const [d, col] of band) {
      col.sort((a, b) => a.id.localeCompare(b.id));
      let y = top + (tallest - heightOf(col)) / 2;
      for (const n of col) {
        n.x = d * COLUMN + (n.kind === "port" && n.direction !== "out" ? NODE_WIDTH.component - NODE_WIDTH.port : 0);
        n.y = y;
        y += n.height + GAP;
      }
    }
    return tallest;
  };
  // The channel rows first, one under the other.
  const channelBands = [...bands.keys()].filter((c) => c !== 0 && c !== COMMAND_BAND).sort((a, b) => a - b);
  const shared = bands.get(0);
  const columnsOf = (keys: number[]) => new Set(keys.flatMap((c) => [...bands.get(c)!.keys()]));
  const rowColumns = columnsOf(channelBands);
  // The whole-instrument blocks stand where the channel rows meet (signal-chain.md 1.2) — centred on
  // the rows — when no column holds both; otherwise they take a band of their own above the rows.
  const meet = !!shared && channelBands.length > 0 && ![...shared.keys()].some((d) => rowColumns.has(d));
  let top = shared && !meet ? place(shared, 0) + BAND_GAP : 0;
  const rowsTop = top;
  for (const c of channelBands) top += place(bands.get(c)!, top) + BAND_GAP;
  if (shared && meet) {
    const rows = top - BAND_GAP - rowsTop;
    const tallest = tallestOf(shared);
    place(shared, rowsTop + Math.max(0, (rows - tallest) / 2));
    top = Math.max(top, rowsTop + tallest + BAND_GAP);
  }
  const command = bands.get(COMMAND_BAND);
  if (command) place(command, top);
  return { nodes, edges: [...edges.values()] };
}
