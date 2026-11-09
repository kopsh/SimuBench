import type {
  InstrumentInternals,
  InternalComponent,
  InternalReading,
} from "@/api/client";

/**
 * What a component does, read from the internals view (#739): what drives it, what it reaches,
 * which readings it shows up in. Pure functions; the graph and the details panel draw them.
 *
 * Links are component-level (`a>b`); a panel port is the node `p:<name>`.
 */

export type Trace = { nodes: ReadonlySet<string>; links: ReadonlySet<string> };

export const link = (a: string, b: string) => `${a}>${b}`;
const owner = (end: string) => end.slice(0, end.lastIndexOf("."));

type Adjacency = Map<string, { to: string; kind: "wire" | "ref" | "select" }[]>;

function adjacency(view: InstrumentInternals): Adjacency {
  const out: Adjacency = new Map();
  const add = (a: string, b: string, kind: "wire" | "ref" | "select") => {
    if (a === b) return;
    const list = out.get(a) ?? [];
    if (!list.some((e) => e.to === b)) list.push({ to: b, kind });
    out.set(a, list);
  };
  for (const w of view.wires) add(owner(w.from), owner(w.to), "wire");
  for (const p of view.ports) {
    if (p.direction === "out") add(owner(p.target), `p:${p.name}`, "wire");
    else add(`p:${p.name}`, owner(p.target), "wire");
  }
  for (const r of view.refs ?? []) add(r.from, r.to, "ref");
  for (const s of view.selects ?? [])
    for (const c of view.components)
      if (c.channel != null) add(c.name, s.component, "select");
  return out;
}

/**
 * The selected component's chain: everything that feeds it (signal wires and what it uses) and
 * everything it reaches, down to the panel outputs and the readers. A channel reaches a component
 * that picks its channel with a setting (a trigger source) only when nothing else leads there, as
 * the server's reading paths do.
 */
export function traceOf(view: InstrumentInternals, name: string): Trace {
  const fwd = adjacency(view);
  const back: Adjacency = new Map();
  for (const [a, list] of fwd)
    for (const e of list)
      if (e.kind !== "select")
        back.set(e.to, [...(back.get(e.to) ?? []), { to: a, kind: e.kind }]);
  const nodes = new Set([name]);
  const links = new Set<string>();
  const walk = (
    adj: Adjacency,
    kinds: string[],
    dir: 1 | -1,
    starts: string[],
  ) => {
    const seen = new Set(starts);
    const queue = [...starts];
    while (queue.length) {
      const cur = queue.shift()!;
      for (const e of adj.get(cur) ?? []) {
        if (!kinds.includes(e.kind)) continue;
        if (e.kind === "select" && nodes.has(e.to)) continue;
        links.add(dir === 1 ? link(cur, e.to) : link(e.to, cur));
        if (seen.has(e.to)) continue;
        seen.add(e.to);
        nodes.add(e.to);
        queue.push(e.to);
      }
    }
  };
  walk(back, ["wire", "ref"], -1, [name]);
  const upstream = new Set(nodes);
  nodes.clear();
  nodes.add(name);
  walk(fwd, ["wire", "ref"], 1, [name]);
  walk(fwd, ["wire", "ref", "select"], 1, [name]);
  for (const n of upstream) nodes.add(n);
  return { nodes, links };
}

/** One reading's route: its components and the links between them. */
export function pathTrace(path: readonly string[]): Trace {
  return {
    nodes: new Set(path),
    links: new Set(path.slice(1).map((b, i) => link(path[i], b))),
  };
}

export const nodesTrace = (names: readonly string[]): Trace => ({
  nodes: new Set(names),
  links: new Set(),
});

/** A query as it is sent: `:WAVeform:DATA` reads `:WAVeform:DATA?`. */
export const queryForm = (header: string) =>
  header.endsWith("?") ? header : `${header}?`;

/** How a command is used: `X` (set only), `X?` (query only), `X[?]` (both). */
export function commandForm(c: {
  header: string;
  set?: boolean;
  query?: boolean;
}): string {
  const set = c.set ?? true;
  const query = c.query ?? true;
  if (set && query) return `${c.header}[?]`;
  return query ? queryForm(c.header) : c.header;
}

export type ControlRow = {
  setting: string;
  commands: string[];
  /** Other components that follow the same setting (one switch, several blocks). */
  alsoDrives: string[];
};

/** What drives a component: one row per setting it follows, with the commands that write it. */
export function controlRows(
  view: InstrumentInternals,
  comp: InternalComponent,
): ControlRow[] {
  return comp.settings.map((setting) => ({
    setting,
    commands: [
      ...new Set(
        comp.commands
          .filter((c) => c.setting === setting)
          .map((c) => commandForm(c)),
      ),
    ],
    alsoDrives: view.components
      .filter((c) => c.name !== comp.name && c.settings.includes(setting))
      .map((c) => c.name),
  }));
}

/** The commands that run one of the component's operations (no setting in between). */
export function operationCommands(comp: InternalComponent): string[] {
  return [
    ...new Set(comp.commands.filter((c) => c.op).map((c) => commandForm(c))),
  ];
}

export type ReadingGroup = {
  provider: string;
  path: string[];
  via: InternalReading["via"];
  selectedBy: string | null;
  /** result: a reader at the end of the chain; self: the component's own queries; onTheWay: a component in between. */
  kind: "result" | "self" | "onTheWay";
  headers: string[];
};

/** The readings a component affects, one group per provider: results first, then its own queries, then the ones on the way. */
export function readingGroups(comp: InternalComponent): ReadingGroup[] {
  const groups = new Map<string, ReadingGroup>();
  for (const r of comp.readings) {
    const g = groups.get(r.provider) ?? {
      provider: r.provider,
      path: r.path?.length ? r.path : [comp.name, r.provider],
      via: r.via,
      selectedBy: r.selected_by ?? null,
      kind:
        r.provider === comp.name
          ? "self"
          : r.terminal
            ? "result"
            : "onTheWay",
      headers: [],
    };
    g.headers.push(queryForm(r.header));
    groups.set(r.provider, g);
  }
  const rank = { result: 0, self: 1, onTheWay: 2 } as const;
  return [...groups.values()].sort(
    (a, b) => rank[a.kind] - rank[b.kind] || a.path.length - b.path.length,
  );
}

/** The channel a source setting names (`C2`, `CHANnel2`, `CHAN2` → 2), or null. */
export function pickedChannel(value: unknown): number | null {
  const m = /(\d+)\s*$/.exec(String(value ?? ""));
  return m ? Number(m[1]) : null;
}

// ---- finding a command or setting ---------------------------------------------------------------

type Node = { long: string; short: string; suffix: "any" | string; optional: boolean };

function parsePattern(header: string): Node[] | null {
  const nodes: Node[] = [];
  const re = /(\[)?:?([A-Za-z*][A-Za-z]*)(<[^>]*>|\d+)?(\])?/g;
  let m: RegExpExecArray | null;
  let optional = false;
  let pos = 0;
  while ((m = re.exec(header))) {
    if (m.index !== pos && header.slice(pos, m.index).replace(/[:\s]/g, "")) return null;
    if (m[1]) optional = true;
    const long = m[2];
    const short = long.startsWith("*") ? long : long.replace(/[a-z].*$/, "");
    const suffix = m[3] ? (m[3].startsWith("<") ? "any" : m[3]) : "";
    nodes.push({ long: long.toUpperCase(), short: short.toUpperCase(), suffix, optional });
    if (m[4]) optional = false;
    pos = re.lastIndex;
  }
  return nodes.length ? nodes : null;
}

/**
 * Whether a typed command names this header, as an instrument would accept it: short or long
 * mnemonics, optional nodes left out, a numeric suffix (`CHAN1`), a trailing `?`, any case.
 */
export function matchesHeader(pattern: string, typed: string): boolean {
  const nodes = parsePattern(pattern);
  const text = typed.trim().split(/\s/)[0].replace(/\?$/, "");
  if (!nodes || !text) return false;
  const tokens = text.replace(/^:/, "").split(":");
  const fits = (n: Node, tok: string) => {
    const m = /^(\*?[A-Za-z]+)(\d*)$/.exec(tok);
    if (!m) return false;
    const word = m[1].toUpperCase();
    if (word !== n.long && word !== n.short) return false;
    if (!m[2]) return true;
    return n.suffix === "any" || n.suffix === m[2];
  };
  const go = (i: number, j: number): boolean => {
    if (j === tokens.length) return nodes.slice(i).every((n) => n.optional);
    if (i === nodes.length) return false;
    if (fits(nodes[i], tokens[j]) && go(i + 1, j + 1)) return true;
    return nodes[i].optional && go(i + 1, j);
  };
  return go(0, 0);
}

export type SearchHit = {
  component: string;
  /** The command header or setting path that matched. */
  text: string;
  kind: "command" | "setting";
};

/**
 * The components a typed command or setting reaches: commands by SCPI matching (or a plain
 * substring while typing), settings by path or label.
 */
export function search(
  view: InstrumentInternals,
  query: string,
  labelOf: (path: string) => string | undefined,
): SearchHit[] {
  const q = query.trim();
  if (!q) return [];
  const lower = q.toLowerCase().replace(/\?$/, "");
  const hits: SearchHit[] = [];
  for (const c of view.components) {
    const seen = new Set<string>();
    for (const cmd of c.commands) {
      if (seen.has(cmd.header)) continue;
      if (
        matchesHeader(cmd.header, q) ||
        cmd.header.toLowerCase().includes(lower)
      ) {
        seen.add(cmd.header);
        hits.push({ component: c.name, text: commandForm(cmd), kind: "command" });
      }
    }
    for (const s of c.settings) {
      const label = labelOf(s)?.toLowerCase();
      if (s.toLowerCase().includes(lower) || label?.includes(lower))
        hits.push({ component: c.name, text: s, kind: "setting" });
    }
  }
  return hits;
}
