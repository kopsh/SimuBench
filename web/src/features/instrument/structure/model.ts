/**
 * The build area's pure logic (#1201, instrument-authoring.md 9): a definition's structure —
 * components, channel components, signal wiring, panel ports — and the edits the canvas makes to
 * it. Every function returns a new structure; the page sends it whole to
 * `PUT /api/definitions/{id}/structure`, and the server keeps only what the file does not inherit.
 */
import type { Schemas } from "@/api/client";
import { typeSearchText } from "@/lib/component";

type StructureComponentOut = Schemas["StructureComponent-Output"];
/** A block as written: its own label and description are optional (#1261), so a new block leaves them out. */
export type StructureComponent = Omit<
  StructureComponentOut,
  "label" | "description"
> &
  Partial<Pick<StructureComponentOut, "label" | "description">>;
export type ComponentDoc = Schemas["ComponentDoc"];

/** The part of a structure the editor changes (what the save carries besides the hash). */
export interface Structure {
  components: StructureComponent[];
  channel_components: StructureComponent[];
  wiring: { instrument: [string, string][]; channel: [string, string][] };
  ports: {
    instrument: Record<string, string>;
    channel: Record<string, string>;
  };
  /** Settings (#1206): the instrument's, and those every channel has one of. */
  state: Record<string, SettingSpec>;
  channel_state: Record<string, SettingSpec>;
  commands: CommandSpec[];
}

/** Where a component lives: the whole instrument, or every channel (one copy each). */
export type Scope = "instrument" | "channel";

/** A component port: which component, in which scope, which port. */
export interface End {
  scope: Scope;
  name: string;
  port: string;
}

/** A signal wire as the canvas draws it: its list, its place in it, and both ends resolved. */
export interface Wire {
  list: Scope;
  index: number;
  from: End;
  to: End;
}

/** The channel prefix instrument-level wiring uses for a channel's component (`ch{n}.probe.out`). */
export const CHANNEL_PREFIX = "ch{n}.";

const pairs = (wires: string[][]): [string, string][] =>
  wires.map((w) => [w[0], w[1]]);

/** The editable part of a structure the server returned. */
export function structureOf(s: {
  components: StructureComponent[];
  channel_components: StructureComponent[];
  wiring: { instrument: string[][]; channel: string[][] };
  ports: Structure["ports"];
  state?: Record<string, SettingSpec>;
  channel_state?: Record<string, SettingSpec>;
  commands?: CommandSpec[];
}): Structure {
  return {
    components: s.components,
    channel_components: s.channel_components,
    wiring: {
      instrument: pairs(s.wiring.instrument),
      channel: pairs(s.wiring.channel),
    },
    ports: { instrument: s.ports.instrument, channel: s.ports.channel },
    state: s.state ?? {},
    channel_state: s.channel_state ?? {},
    commands: s.commands ?? [],
  };
}

function listOf(s: Structure, scope: Scope): StructureComponent[] {
  return scope === "instrument" ? s.components : s.channel_components;
}

function withList(
  s: Structure,
  scope: Scope,
  list: StructureComponent[],
): Structure {
  return scope === "instrument"
    ? { ...s, components: list }
    : { ...s, channel_components: list };
}

/** The node id of a component on the canvas (names are unique across both scopes). */
export function nodeId(scope: Scope, name: string): string {
  return `${scope === "instrument" ? "i" : "c"}:${name}`;
}

export function parseNodeId(id: string): { scope: Scope; name: string } | null {
  if (id.startsWith("i:")) return { scope: "instrument", name: id.slice(2) };
  if (id.startsWith("c:")) return { scope: "channel", name: id.slice(2) };
  return null;
}

/** `name.port` → its two halves (a component name has no dot). */
function splitRef(ref: string): { name: string; port: string } | null {
  const dot = ref.indexOf(".");
  if (dot <= 0 || dot === ref.length - 1) return null;
  return { name: ref.slice(0, dot), port: ref.slice(dot + 1) };
}

/** One end of a wire as written in `list`; null when it names no known component. */
export function parseEnd(s: Structure, list: Scope, text: string): End | null {
  if (list === "instrument" && text.startsWith(CHANNEL_PREFIX)) {
    const ref = splitRef(text.slice(CHANNEL_PREFIX.length));
    return ref && s.channel_components.some((c) => c.name === ref.name)
      ? { scope: "channel", ...ref }
      : null;
  }
  const ref = splitRef(text);
  if (!ref) return null;
  return listOf(s, list).some((c) => c.name === ref.name)
    ? { scope: list, ...ref }
    : null;
}

/** An end written for a wire in `list`. */
function endText(end: End, list: Scope): string {
  return list === "instrument" && end.scope === "channel"
    ? `${CHANNEL_PREFIX}${end.name}.${end.port}`
    : `${end.name}.${end.port}`;
}

/** Every wire whose two ends name components (a wire to nothing is left for the diagnostics). */
export function wiresOf(s: Structure): Wire[] {
  const out: Wire[] = [];
  for (const list of ["instrument", "channel"] as const) {
    s.wiring[list].forEach(([a, b], index) => {
      const from = parseEnd(s, list, a);
      const to = parseEnd(s, list, b);
      if (from && to) out.push({ list, index, from, to });
    });
  }
  return out;
}

/** All component names, both scopes. */
export function names(s: Structure): Set<string> {
  return new Set([...s.components, ...s.channel_components].map((c) => c.name));
}

/** A name for a new component of `type`: the type itself, else `type2`, `type3` … */
export function freeName(s: Structure, type: string): string {
  const taken = names(s);
  if (!taken.has(type)) return type;
  for (let n = 2; ; n++) if (!taken.has(`${type}${n}`)) return `${type}${n}`;
}

export function addComponent(
  s: Structure,
  type: string,
  scope: Scope,
): { structure: Structure; name: string } {
  const name = freeName(s, type);
  return {
    structure: withList(s, scope, [
      ...listOf(s, scope),
      { name, type, params: {} },
    ]),
    name,
  };
}

/** What deleting a component also takes with it: the counts the confirmation names. */
export interface RemovalCounts {
  wires: number;
  ports: number;
}

function touches(end: End | null, scope: Scope, name: string): boolean {
  return !!end && end.scope === scope && end.name === name;
}

function portTargets(
  panel: Scope,
  target: string,
  scope: Scope,
  name: string,
): boolean {
  const ref = splitRef(target);
  return panel === scope && !!ref && ref.name === name;
}

export function removalCounts(
  s: Structure,
  scope: Scope,
  name: string,
): RemovalCounts {
  const wires = (["instrument", "channel"] as const).reduce(
    (n, list) =>
      n +
      s.wiring[list].filter(
        ([a, b]) =>
          touches(parseEnd(s, list, a), scope, name) ||
          touches(parseEnd(s, list, b), scope, name),
      ).length,
    0,
  );
  const ports = (["instrument", "channel"] as const).reduce(
    (n, panel) =>
      n +
      Object.values(s.ports[panel]).filter((t) =>
        portTargets(panel, t, scope, name),
      ).length,
    0,
  );
  return { wires, ports };
}

/** Delete a component, its wires and the panel ports that lead to it; a `refs` parameter that
 * named it is emptied (the diagnostics then say a required one is missing). */
export function removeComponent(
  s: Structure,
  scope: Scope,
  name: string,
  docs: ReadonlyMap<string, ComponentDoc> = new Map(),
): Structure {
  const keepWire =
    (list: Scope) =>
    ([a, b]: [string, string]) =>
      !touches(parseEnd(s, list, a), scope, name) &&
      !touches(parseEnd(s, list, b), scope, name);
  const keepPorts = (panel: Scope) =>
    Object.fromEntries(
      Object.entries(s.ports[panel]).filter(
        ([, t]) => !portTargets(panel, t, scope, name),
      ),
    );
  const unref = (c: StructureComponent): StructureComponent => {
    const refs = new Set((docs.get(c.type)?.refs ?? []).map((r) => r.param));
    const params = Object.fromEntries(
      Object.entries(c.params ?? {}).filter(
        ([k, v]) => !(refs.has(k) && v === name),
      ),
    );
    return Object.keys(params).length === Object.keys(c.params ?? {}).length
      ? c
      : { ...c, params };
  };
  const next: Structure = {
    ...s,
    components: s.components
      .filter((c) => scope !== "instrument" || c.name !== name)
      .map(unref),
    channel_components: s.channel_components
      .filter((c) => scope !== "channel" || c.name !== name)
      .map(unref),
    wiring: {
      instrument: s.wiring.instrument.filter(keepWire("instrument")),
      channel: s.wiring.channel.filter(keepWire("channel")),
    },
    ports: {
      instrument: keepPorts("instrument"),
      channel: keepPorts("channel"),
    },
  };
  return next;
}

/** The list a wire between two ends is written in: inside a channel when both ends are a
 * channel's, else at instrument level. */
export function wireList(from: End, to: End): Scope {
  return from.scope === "channel" && to.scope === "channel"
    ? "channel"
    : "instrument";
}

/** Add a wire `from` → `to`; the same wire twice, or a wire into an input that already has one, is
 * not added (null). */
export function connect(s: Structure, from: End, to: End): Structure | null {
  const list = wireList(from, to);
  const a = endText(from, list);
  const b = endText(to, list);
  if (from.scope === to.scope && from.name === to.name) return null;
  const taken = wiresOf(s).some(
    (w) =>
      w.to.scope === to.scope && w.to.name === to.name && w.to.port === to.port,
  );
  if (taken || s.wiring[list].some(([x, y]) => x === a && y === b)) return null;
  return { ...s, wiring: { ...s.wiring, [list]: [...s.wiring[list], [a, b]] } };
}

export function removeWire(
  s: Structure,
  list: Scope,
  index: number,
): Structure {
  return {
    ...s,
    wiring: {
      ...s.wiring,
      [list]: s.wiring[list].filter((_, i) => i !== index),
    },
  };
}

/** Set a parameter's value, or remove it (`undefined`: the component's default applies). */
export function setParam(
  s: Structure,
  scope: Scope,
  name: string,
  key: string,
  value: unknown,
): Structure {
  return withList(
    s,
    scope,
    listOf(s, scope).map((c) => {
      if (c.name !== name) return c;
      const params = { ...(c.params ?? {}) };
      if (value === undefined) delete params[key];
      else params[key] = value;
      return { ...c, params };
    }),
  );
}

/** A panel port's name for a channel table holds `{n}` (`CH{n}`). */
export function panelPortName(raw: string, panel: Scope): string | null {
  const name = raw.trim();
  if (!name || name.includes(" ")) return null;
  if (panel === "channel" && !name.includes("{n}")) return `${name}{n}`;
  return name;
}

export function setPanelPort(
  s: Structure,
  panel: Scope,
  name: string,
  target: string,
): Structure {
  return {
    ...s,
    ports: { ...s.ports, [panel]: { ...s.ports[panel], [name]: target } },
  };
}

export function removePanelPort(
  s: Structure,
  panel: Scope,
  name: string,
): Structure {
  const table = { ...s.ports[panel] };
  delete table[name];
  return { ...s, ports: { ...s.ports, [panel]: table } };
}

// ---- ports of a component type ------------------------------------------------------------------

export interface PortRow {
  name: string;
  direction: string;
  medium: string;
  /** A port the type's `port_pattern` creates, offered as the next free one. */
  extra?: boolean;
}

/** The prefixes of a numbered port pattern (`in\d+` → in; `(in|out)\d+` → in, out); empty for a
 * pattern of another shape, whose ports come only from the wiring. */
export function patternPrefixes(pattern: string | null | undefined): string[] {
  if (!pattern || !pattern.endsWith("\\d+")) return [];
  const head = pattern.slice(0, -3);
  const alt =
    head.startsWith("(") && head.endsWith(")")
      ? head.slice(1, -1).split("|")
      : [head];
  return alt.every((p) => /^[A-Za-z_]+$/.test(p)) ? alt : [];
}

/** A component's ports: its type's own, those the wiring created from its pattern, and the next
 * free one of each prefix (so a wire can be dragged to it). */
export function portsOf(
  s: Structure,
  scope: Scope,
  name: string,
  doc: ComponentDoc | undefined,
): PortRow[] {
  const rows: PortRow[] = (doc?.ports ?? []).map((p) => ({
    name: p.name,
    direction: p.direction,
    medium: p.medium,
  }));
  const used = new Set<string>();
  for (const w of wiresOf(s)) {
    for (const end of [w.from, w.to])
      if (end.scope === scope && end.name === name) used.add(end.port);
  }
  const own = new Set(rows.map((r) => r.name));
  for (const prefix of patternPrefixes(doc?.port_pattern)) {
    const direction = prefix.startsWith("out") ? "out" : "in";
    const numbers = [...used]
      .filter(
        (p) => p.startsWith(prefix) && /^\d+$/.test(p.slice(prefix.length)),
      )
      .map((p) => Number(p.slice(prefix.length)))
      .sort((a, b) => a - b);
    for (const n of numbers)
      rows.push({ name: `${prefix}${n}`, direction, medium: "any" });
    rows.push({
      name: `${prefix}${(numbers.at(-1) ?? 0) + 1}`,
      direction,
      medium: "any",
      extra: true,
    });
  }
  for (const p of [...used].sort()) {
    if (!own.has(p) && !rows.some((r) => r.name === p))
      rows.push({ name: p, direction: "in", medium: "any" });
  }
  return rows;
}

/** Can a wire run from port `a` (an output) to port `b` (an input)? Media must agree; a port of a
 * pattern takes any. Electrical pins lead to the panel, not to a wire. */
export function canWire(
  a: PortRow | undefined,
  b: PortRow | undefined,
): boolean {
  if (
    !a ||
    !b ||
    a.direction !== "out" ||
    (b.direction !== "in" && b.direction !== "ref")
  )
    return false;
  if (a.medium === "electrical" || b.medium === "electrical") return false;
  return a.medium === "any" || b.medium === "any" || a.medium === b.medium;
}

// ---- parameters --------------------------------------------------------------------------------

/** How a parameter is written: a literal the build area edits, a setting it follows (read only
 * here), or a component it names (a `refs` parameter). */
export type ParamKind =
  | { kind: "literal"; value: unknown; set: boolean }
  | { kind: "setting"; setting: string }
  | {
      kind: "ref";
      value: string | null;
      scope: Scope;
      optional: boolean;
      target: string | null;
    };

export function paramKind(
  c: StructureComponent,
  doc: ComponentDoc | undefined,
  key: string,
): ParamKind {
  const ref = doc?.refs?.find((r) => r.param === key);
  const value = (c.params ?? {})[key];
  if (ref) {
    return {
      kind: "ref",
      value: typeof value === "string" ? value : null,
      scope: ref.scope,
      optional: ref.optional ?? false,
      target: ref.target ?? null,
    };
  }
  if (typeof value === "string" && value.startsWith("$"))
    return { kind: "setting", setting: value.slice(1) };
  if (
    value &&
    typeof value === "object" &&
    "from" in (value as Record<string, unknown>)
  ) {
    const from = (value as Record<string, unknown>).from;
    return {
      kind: "setting",
      setting: typeof from === "string" ? from.replace(/^\$/, "") : "?",
    };
  }
  const set = key in (c.params ?? {});
  const fallback = doc?.params.find((p) => p.name === key)?.default;
  return { kind: "literal", value: set ? value : fallback, set };
}

/** The parameter names to list for a component: its type's, then any the definition writes that
 * the type does not declare (the diagnostics flag those). */
export function paramNames(
  c: StructureComponent,
  doc: ComponentDoc | undefined,
): string[] {
  const declared = (doc?.params ?? []).map((p) => p.name);
  const refs = (doc?.refs ?? [])
    .map((r) => r.param)
    .filter((r) => !declared.includes(r));
  const extra = Object.keys(c.params ?? {}).filter(
    (k) => !declared.includes(k) && !refs.includes(k),
  );
  return [...declared, ...refs, ...extra];
}

/** Parse a typed literal like the default's kind: a number for a number (with an SI prefix:
 * `10M`, `1.5k`, `100u`, #1214), true/false for a boolean, else text. An empty entry means "back
 * to the default". */
export function parseLiteral(
  typed: string,
  like: unknown,
): { ok: true; value: unknown } | { ok: false } {
  const text = typed.trim();
  if (text === "") return { ok: true, value: undefined };
  if (typeof like === "number") {
    if (text === "INF" || text === "-INF") return { ok: true, value: text };
    const n = parseNumber(text);
    return n === null ? { ok: false } : { ok: true, value: n };
  }
  if (typeof like === "boolean") {
    if (text === "true") return { ok: true, value: true };
    if (text === "false") return { ok: true, value: false };
    return { ok: false };
  }
  if (like !== null && typeof like === "object") {
    try {
      return { ok: true, value: JSON.parse(text) };
    } catch {
      return { ok: false };
    }
  }
  return { ok: true, value: text };
}

export function literalText(value: unknown): string {
  if (value === undefined || value === null) return "";
  if (typeof value === "object") return JSON.stringify(value);
  if (typeof value === "number") return formatNumber(value);
  return String(value);
}

// ---- numbers with an SI prefix (#1214) ---------------------------------------------------------

const PREFIXES: Record<string, number> = {
  p: -12,
  n: -9,
  u: -6,
  µ: -6,
  m: -3,
  k: 3,
  M: 6,
  G: 9,
  T: 12,
};
const NUMBER = /^([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*([pnuµmkMGT])?$/;

/** A number as typed: plain (`2.5e6`) or with an SI prefix (`10M`, `1.5k`, `100u`; `m` is milli,
 * `M` mega); null when it is not one. */
export function parseNumber(text: string): number | null {
  const m = NUMBER.exec(text.trim());
  if (!m) return null;
  const base = Number(m[1]);
  if (!Number.isFinite(base)) return null;
  // Scale by a power of ten written out, so 1.5k is exactly 1500.
  if (!m[2]) return base;
  return m[1].toLowerCase().includes("e")
    ? base * 10 ** PREFIXES[m[2]]
    : Number(`${m[1]}e${PREFIXES[m[2]]}`);
}

/** A number written with an SI prefix where one reads better (10000000 → 10M, 0.0001 → 100u)
 * and gives back the same number; else as it is. */
export function formatNumber(n: number): string {
  const plain = String(n);
  const abs = Math.abs(n);
  if (!Number.isFinite(n) || abs === 0 || (abs >= 1e-3 && abs < 1e3))
    return plain;
  const exp = Math.floor(Math.log10(abs) / 3) * 3;
  const prefix = Object.entries(PREFIXES).find(
    ([k, e]) => e === exp && k !== "µ",
  )?.[0];
  if (!prefix) return plain;
  const mantissa = Number((n / 10 ** exp).toPrecision(12));
  const text = `${mantissa}${prefix}`;
  return parseNumber(text) === n ? text : plain;
}

// ---- table parameters (#1214) ------------------------------------------------------------------

/** A table a parameter holds: a mapping of plain values (a range → a shunt), edited row by row. */
export function isTable(
  value: unknown,
): value is Record<string, string | number | boolean> {
  return (
    !!value &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    Object.values(value as Record<string, unknown>).every((v) =>
      ["string", "number", "boolean"].includes(typeof v),
    )
  );
}

/** The table with row `key` set (renamed to `to` when given); the row keeps its place. */
export function setTableRow(
  table: Record<string, unknown>,
  key: string,
  value: unknown,
  to = key,
): Record<string, unknown> {
  if (!(key in table)) return { ...table, [to]: value };
  return Object.fromEntries(
    Object.entries(table).map(([k, v]) => (k === key ? [to, value] : [k, v])),
  );
}

export function removeTableRow(
  table: Record<string, unknown>,
  key: string,
): Record<string, unknown> {
  return Object.fromEntries(Object.entries(table).filter(([k]) => k !== key));
}

/** A table key as typed: a number written plainly (the server makes it a number again, #1214). */
export function tableKey(raw: string): string | null {
  const text = raw.trim();
  if (!text) return null;
  const n = parseNumber(text);
  return n === null ? text : String(n);
}

// ---- the component library (#1214) -------------------------------------------------------------

/** The library's families, in the order it lists them. */
export const LIBRARY_GROUPS = [
  "signal",
  "measure",
  "source",
  "scope",
  "trigger",
  "system",
  "other",
] as const;
export type LibraryGroup = (typeof LIBRARY_GROUPS)[number];

/** The internal components matching `query` (type, names, descriptions in both languages), by family. */
/** The library's sections (#1214, P2-4): with the definition's class, the types its class's
 * built-in models use come first as their own section (`own`); the other types follow in their
 * families, and are left out (`hidden` counts them) unless `all` or a search asks for them. */
export function libraryGroups(
  docs: readonly ComponentDoc[],
  query: string,
  cls: string | null = null,
  all = true,
): { groups: { group: LibraryGroup | "own"; items: ComponentDoc[] }[]; hidden: number } {
  const q = query.trim().toLowerCase();
  const shown = docs.filter((d) => d.category === "internal" && (!q || typeSearchText(d).some((f) => f.toLowerCase().includes(q))));
  const isOwn = (d: ComponentDoc) => cls !== null && (d.classes ?? []).includes(cls);
  const own = shown.filter(isOwn);
  const rest = shown.filter((d) => !isOwn(d));
  const open = all || q !== "" || cls === null;
  const groups: { group: LibraryGroup | "own"; items: ComponentDoc[] }[] = own.length ? [{ group: "own", items: own }] : [];
  if (open) {
    for (const group of LIBRARY_GROUPS) {
      const items = rest.filter((d) => (d.group ?? "other") === group);
      if (items.length) groups.push({ group, items });
    }
  }
  return { groups, hidden: open ? 0 : rest.length };
}

/** A component type's ports in one line: inputs → outputs (`in → out`; a pattern as `in1…`). */
export function portSignature(doc: ComponentDoc): string {
  const ins = doc.ports
    .filter((p) => p.direction !== "out" && p.medium !== "electrical")
    .map((p) => p.name);
  const outs = doc.ports
    .filter((p) => p.direction === "out" && p.medium !== "electrical")
    .map((p) => p.name);
  for (const prefix of patternPrefixes(doc.port_pattern))
    (prefix.startsWith("out") ? outs : ins).push(`${prefix}1…`);
  const pins = doc.ports
    .filter((p) => p.medium === "electrical")
    .map((p) => p.name);
  const flow =
    ins.length || outs.length
      ? `${ins.join(", ") || "—"} → ${outs.join(", ") || "—"}`
      : "";
  return [flow, pins.length ? `⊡ ${pins.join(", ")}` : ""]
    .filter(Boolean)
    .join("  ");
}

/** Does any of `fields` contain the query (case-insensitive)? An empty query matches. */
export function matches(
  query: string,
  ...fields: (string | null | undefined)[]
): boolean {
  const q = query.trim().toLowerCase();
  return !q || fields.some((f) => (f ?? "").toLowerCase().includes(q));
}

// ---- diagnostics -------------------------------------------------------------------------------

/** The component a diagnostic's pointer points at (`/components/3/params/x` → that component). */
export function diagnosticNode(
  s: Structure,
  pointer: string | null | undefined,
): string | null {
  if (!pointer) return null;
  const parts = pointer.split("/");
  const index = Number(parts[2]);
  if (!Number.isInteger(index)) return null;
  if (parts[1] === "components" && s.components[index])
    return nodeId("instrument", s.components[index].name);
  if (parts[1] === "channel_components" && s.channel_components[index])
    return nodeId("channel", s.channel_components[index].name);
  return null;
}

// ---- layout ------------------------------------------------------------------------------------

export interface Point {
  x: number;
  y: number;
}

export const COLUMN = 260;
export const ROW = 150;
/** The vertical gap between two blocks in a column or between two grid rows. */
export const GAP_Y = 36;

// A block's size on the canvas (#1216): the canvas draws it and the layout spaces it from the
// same numbers, so no combination of ports and settings makes two blocks overlap.
export const NODE_WIDTH = 220;
export const NODE_HEAD = 44;
export const PORT_ROW = 22;
export const FOLLOW_ROW = 18;
/** At most this many followed settings on a block; the rest as "+n". */
export const FOLLOW_ROWS = 3;

/** The height of a block with these ports, panel ports and followed settings. */
export function blockHeight(
  rows: PortRow[],
  panel: number,
  follows: number,
): number {
  const ins = rows.filter((r) => r.direction !== "out").length;
  const outs = rows.length - ins;
  const ports = Math.max(ins, outs, 1) * PORT_ROW;
  const panelRow = panel ? PORT_ROW : 0;
  const followRows = follows
    ? FOLLOW_ROW * Math.min(follows, FOLLOW_ROWS + 1) + 4
    : 0;
  return NODE_HEAD + ports + panelRow + followRows + 6;
}

/** The panel ports that lead to a component. */
export function panelPortsOf(
  s: Structure,
  scope: Scope,
  name: string,
): string[] {
  return Object.entries(s.ports[scope])
    .filter(([, target]) => target.split(".")[0] === name)
    .map(([port]) => port);
}

/** The height a component's block is drawn at. */
export function nodeHeight(
  s: Structure,
  scope: Scope,
  c: StructureComponent,
  docs: ReadonlyMap<string, ComponentDoc>,
): number {
  return blockHeight(
    portsOf(s, scope, c.name, docs.get(c.type)),
    panelPortsOf(s, scope, c.name).length,
    followedSettings(c).length,
  );
}
/** Where the instrument's components start below the channel band. */
export const BAND_GAP = 80;

/** A column for every component on a signal chain, from its wires (left to right, a source in
 * column 0; a wire that loops back does not push), one row per column entry. The components on
 * no chain (a calibration store, a trigger model, a reading buffer) go in a grid below the chains,
 * wrapped to the canvas width, each one followed by the components it refers to (#1212). The
 * channel components lie in a band on top. A position the user dragged to wins. */
export function autoLayout(
  s: Structure,
  saved: Record<string, Point> = {},
  docs: ReadonlyMap<string, ComponentDoc> = new Map(),
): Record<string, Point> {
  const out: Record<string, Point> = {};
  const ids = [
    ...s.channel_components.map((c) => nodeId("channel", c.name)),
    ...s.components.map((c) => nodeId("instrument", c.name)),
  ];
  const into = new Map<string, string[]>(ids.map((id) => [id, []]));
  const chained = new Set<string>();
  for (const w of wiresOf(s)) {
    const a = nodeId(w.from.scope, w.from.name);
    const b = nodeId(w.to.scope, w.to.name);
    into.get(b)?.push(a);
    chained.add(a).add(b);
  }
  const col = new Map<string, number>();
  const visiting = new Set<string>();
  const depth = (id: string): number => {
    const known = col.get(id);
    if (known !== undefined) return known;
    if (visiting.has(id)) return 0;
    visiting.add(id);
    const d = Math.max(-1, ...(into.get(id) ?? []).map(depth)) + 1;
    visiting.delete(id);
    col.set(id, d);
    return d;
  };
  ids.forEach(depth);
  const place = (scope: Scope, top: number): number => {
    const comps = listOf(s, scope);
    const height = new Map(
      comps.map((c) => [nodeId(scope, c.name), nodeHeight(s, scope, c, docs)]),
    );
    const own = comps.map((c) => nodeId(scope, c.name));
    // Each chain column stacks its blocks by their heights.
    const below = new Map<number, number>();
    for (const id of own.filter((i) => chained.has(i))) {
      const c = col.get(id) ?? 0;
      const y = below.get(c) ?? top;
      below.set(c, y + (height.get(id) ?? ROW) + GAP_Y);
      out[id] = saved[id] ?? { x: c * COLUMN, y };
    }
    const chainBottom = Math.max(top, ...below.values());
    const loose = looseOrder(
      s,
      scope,
      own.filter((i) => !chained.has(i)),
      docs,
    );
    const columns = Math.max(
      0,
      ...[...chained].map((i) => (col.get(i) ?? 0) + 1),
    );
    const width = Math.max(
      columns,
      Math.ceil(Math.sqrt(loose.length * 2)),
      Math.min(loose.length, 3),
      1,
    );
    // The grid: each row as tall as its tallest block.
    let y = below.size ? chainBottom + LOOSE_GAP : top;
    for (let i = 0; i < loose.length; i += width) {
      const row = loose.slice(i, i + width);
      row.forEach((id, k) => (out[id] = saved[id] ?? { x: k * COLUMN, y }));
      y += Math.max(...row.map((id) => height.get(id) ?? ROW)) + GAP_Y;
    }
    return loose.length ? y : Math.max(chainBottom, top + ROW);
  };
  const band = place("channel", 0);
  place(
    "instrument",
    s.channel_components.length ? band - GAP_Y + BAND_GAP : 0,
  );
  return out;
}

/** The components on no chain, in definition order, each followed by those it refers to that
 * are on no chain either, so a reference is drawn between neighbours. */
function looseOrder(
  s: Structure,
  scope: Scope,
  loose: string[],
  docs: ReadonlyMap<string, ComponentDoc>,
): string[] {
  const pending = new Set(loose);
  const order: string[] = [];
  const targets = new Map<string, string[]>();
  for (const r of refLinks(s, docs))
    targets.set(r.from, [...(targets.get(r.from) ?? []), r.to]);
  const visit = (id: string) => {
    if (!pending.has(id)) return;
    pending.delete(id);
    order.push(id);
    for (const to of targets.get(id) ?? [])
      if (to.startsWith(scope === "instrument" ? "i:" : "c:")) visit(to);
  };
  loose.forEach(visit);
  return order;
}

/** The gap between the signal chains and the grid of the other components. */
export const LOOSE_GAP = 40;

// ---- what the canvas draws besides the signal wires (#1212) ----------------------------------

/** A reference from one component to another: a `refs` parameter naming it (`store: cal`). */
export interface RefLink {
  from: string;
  to: string;
  param: string;
}

/** Every reference whose target exists, as node ids. */
export function refLinks(
  s: Structure,
  docs: ReadonlyMap<string, ComponentDoc>,
): RefLink[] {
  const out: RefLink[] = [];
  for (const scope of ["instrument", "channel"] as const) {
    for (const c of listOf(s, scope)) {
      for (const r of docs.get(c.type)?.refs ?? []) {
        const v = (c.params ?? {})[r.param];
        if (typeof v !== "string") continue;
        // As Component.peer: a channel component finds a name in its own channel first.
        const where: Scope[] =
          scope === "channel" && r.scope === "instrument"
            ? ["channel", "instrument"]
            : [r.scope];
        const at = where.find((w) => listOf(s, w).some((x) => x.name === v));
        if (!at || (at === scope && v === c.name)) continue;
        out.push({
          from: nodeId(scope, c.name),
          to: nodeId(at, v),
          param: r.param,
        });
      }
    }
  }
  return out;
}

/** The settings a component follows, one per setting, with the parameters that follow it. */
export function followedSettings(
  c: StructureComponent,
): { setting: string; params: string[] }[] {
  const by = new Map<string, string[]>();
  for (const [k, v] of Object.entries(c.params ?? {})) {
    const setting = boundSetting(v);
    if (setting) by.set(setting, [...(by.get(setting) ?? []), k]);
  }
  return [...by].map(([setting, params]) => ({ setting, params }));
}

/** The components (node ids) with a parameter that follows the setting at `path`. */
export function followers(s: Structure, path: string): string[] {
  const of = (scope: Scope) =>
    listOf(s, scope)
      .filter((c) =>
        Object.values(c.params ?? {}).some((v) => boundSetting(v) === path),
      )
      .map((c) => nodeId(scope, c.name));
  return [...of("channel"), ...of("instrument")];
}

/** The band the channel components are drawn in: a box around them with room to drop more. */
export function channelBand(
  positions: Record<string, Point>,
  s: Structure,
  width = 220,
): { x: number; y: number; w: number; h: number } {
  const pts = s.channel_components
    .map((c) => positions[nodeId("channel", c.name)])
    .filter(Boolean);
  if (!pts.length) return { x: -30, y: -50, w: COLUMN * 2, h: ROW };
  const xs = pts.map((p) => p.x);
  const ys = pts.map((p) => p.y);
  const x = Math.min(...xs) - 30;
  const y = Math.min(...ys) - 50;
  return {
    x,
    y,
    w: Math.max(...xs) + width + 30 - x + COLUMN / 2,
    h: Math.max(...ys) + ROW - y,
  };
}

export function inBand(
  p: Point,
  band: { x: number; y: number; w: number; h: number },
): boolean {
  return (
    p.x >= band.x &&
    p.x <= band.x + band.w &&
    p.y >= band.y &&
    p.y <= band.y + band.h
  );
}

// ---- settings (#1206) --------------------------------------------------------------------------

/** A setting's declaration as the definition writes it (StateSpec); the build area edits the
 * common fields and keeps the others as they are. */
export type SettingSpec = Record<string, unknown>;

export const SETTING_TYPES = [
  "float",
  "int",
  "bool",
  "enum",
  "string",
] as const;
export type SettingType = (typeof SETTING_TYPES)[number];

function settingsOf(s: Structure, scope: Scope): Record<string, SettingSpec> {
  return scope === "instrument" ? s.state : s.channel_state;
}

function withSettings(
  s: Structure,
  scope: Scope,
  table: Record<string, SettingSpec>,
): Structure {
  return scope === "instrument"
    ? { ...s, state: table }
    : { ...s, channel_state: table };
}

/** The path a parameter or a command names a setting by: `level`, or `ch.level` for a channel's. */
export function settingPath(scope: Scope, name: string): string {
  return scope === "channel" ? `ch.${name}` : name;
}

/** A setting name: letters, digits, `_` and `.` (groups like `out.level`), not starting with `ch.`. */
export function settingName(raw: string): string | null {
  const name = raw.trim();
  if (
    !/^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$/.test(name) ||
    name.startsWith("ch.")
  )
    return null;
  return name;
}

export function newSetting(type: SettingType): SettingSpec {
  switch (type) {
    case "bool":
      return { type, default: false };
    case "enum":
      return { type, options: ["A", "B"], default: "A" };
    case "string":
      return { type, default: "" };
    default:
      return { type, default: 0 };
  }
}

export function addSetting(
  s: Structure,
  scope: Scope,
  name: string,
  type: SettingType,
): Structure | null {
  if (name in settingsOf(s, scope)) return null;
  return withSettings(s, scope, {
    ...settingsOf(s, scope),
    [name]: newSetting(type),
  });
}

/** Change fields of a setting; `undefined` removes a field. A new type resets what only the old
 * type had (options, min, max, unit) and gives a default of the new kind. */
export function updateSetting(
  s: Structure,
  scope: Scope,
  name: string,
  patch: SettingSpec,
): Structure {
  const old = settingsOf(s, scope)[name] ?? {};
  let spec: SettingSpec = { ...old };
  if (patch.type !== undefined && patch.type !== old.type) {
    const fresh = newSetting(patch.type as SettingType);
    spec = Object.fromEntries(
      Object.entries(spec).filter(
        ([k]) =>
          ![
            "options",
            "min",
            "max",
            "unit",
            "ladder",
            "default",
            "rst",
          ].includes(k),
      ),
    );
    spec = { ...spec, ...fresh };
  }
  for (const [k, v] of Object.entries(patch)) {
    if (k === "type") continue;
    if (v === undefined) delete spec[k];
    else spec[k] = v;
  }
  return withSettings(s, scope, { ...settingsOf(s, scope), [name]: spec });
}

export function removeSetting(
  s: Structure,
  scope: Scope,
  name: string,
): Structure {
  const table = { ...settingsOf(s, scope) };
  delete table[name];
  return withSettings(s, scope, table);
}

/** The setting a parameter value follows (`$x`, or a mapping `{from: "$x"}`), or null. */
export function boundSetting(value: unknown): string | null {
  if (typeof value === "string" && value.startsWith("$")) return value.slice(1);
  if (
    value &&
    typeof value === "object" &&
    typeof (value as { from?: unknown }).from === "string"
  ) {
    return (value as { from: string }).from.replace(/^\$/, "");
  }
  return null;
}

/** How many component parameters and commands name a setting: what deleting it leaves dangling. */
export function settingUses(
  s: Structure,
  scope: Scope,
  name: string,
): { params: number; commands: number } {
  const path = settingPath(scope, name);
  const comps =
    scope === "instrument"
      ? [...s.components, ...s.channel_components]
      : s.channel_components;
  const params = comps.reduce(
    (n, c) =>
      n +
      Object.values(c.params ?? {}).filter((v) => boundSetting(v) === path)
        .length,
    0,
  );
  const commands = s.commands.filter((c) => c.state === path).length;
  return { params, commands };
}

/** Who uses a setting: the components with a parameter that follows it, the commands bound to it. */
export function settingUsers(
  s: Structure,
  scope: Scope,
  name: string,
): { components: string[]; commands: string[] } {
  const path = settingPath(scope, name);
  const comps =
    scope === "instrument"
      ? [...s.components, ...s.channel_components]
      : s.channel_components;
  return {
    components: comps
      .filter((c) =>
        Object.values(c.params ?? {}).some((v) => boundSetting(v) === path),
      )
      .map((c) => c.name),
    commands: s.commands
      .filter((c) => c.state === path)
      .map((c) => c.header ?? ""),
  };
}

/** The settings a component's parameter can follow: a channel component its channel's and the
 * instrument's, an instrument component the instrument's. */
export function settingChoices(s: Structure, scope: Scope): string[] {
  const own = Object.keys(s.state).map((n) => settingPath("instrument", n));
  return scope === "channel"
    ? [
        ...Object.keys(s.channel_state).map((n) => settingPath("channel", n)),
        ...own,
      ]
    : own;
}

// ---- commands (#1206) --------------------------------------------------------------------------

/** A command as the definition writes it (CommandSpec); the build area edits the header, what it
 * is bound to and whether it can be set and queried, and keeps the other fields. */
export type CommandSpec = Record<string, unknown> & {
  header?: string;
  state?: string;
  op?: string;
  set?: boolean;
  query?: boolean;
};

export type CommandKind = "state" | "op";

/** What a command does, for the list: a setting, an operation, or something else the agent wrote. */
export function commandTarget(c: CommandSpec): {
  kind: CommandKind | "other";
  target: string;
} {
  if (typeof c.state === "string") return { kind: "state", target: c.state };
  if (typeof c.op === "string") return { kind: "op", target: c.op };
  if (typeof c.read === "string") return { kind: "other", target: c.read };
  return { kind: "other", target: "" };
}

/** Every operation a command can run: `<component>.<op>`, `ch.<component>.<op>` for a channel's. */
export function operationChoices(
  s: Structure,
  docs: ReadonlyMap<string, ComponentDoc>,
): string[] {
  const of = (scope: Scope) => (c: StructureComponent) =>
    (docs.get(c.type)?.operations ?? []).map(
      (o) => `${scope === "channel" ? "ch." : ""}${c.name}.${o.name}`,
    );
  return [
    ...s.components.flatMap(of("instrument")),
    ...s.channel_components.flatMap(of("channel")),
  ];
}

/** Every setting a command can bind: `level`, `ch.level`. */
export function commandSettingChoices(s: Structure): string[] {
  return [
    ...Object.keys(s.state),
    ...Object.keys(s.channel_state).map((n) => `ch.${n}`),
  ];
}

/** A header as typed: no spaces, not empty (the definition checks do the rest, E-DEF-HEADER). */
export function commandHeader(raw: string): string | null {
  const h = raw.trim();
  return h && !/\s/.test(h) ? h : null;
}

export function addCommand(
  s: Structure,
  header: string,
  kind: CommandKind,
  target: string,
): Structure | null {
  if (s.commands.some((c) => c.header === header)) return null;
  const cmd: CommandSpec =
    kind === "state"
      ? { header, state: target }
      : { header, op: target, query: false };
  return { ...s, commands: [...s.commands, cmd] };
}

/** Change fields of command `index`; `undefined` removes a field. Binding it to a setting drops
 * its operation and the other way round. */
export function updateCommand(
  s: Structure,
  index: number,
  patch: CommandSpec,
): Structure {
  return {
    ...s,
    commands: s.commands.map((c, i) => {
      if (i !== index) return c;
      const next: CommandSpec = { ...c };
      if (patch.state !== undefined) delete next.op;
      if (patch.op !== undefined) delete next.state;
      for (const [k, v] of Object.entries(patch)) {
        if (v === undefined) delete next[k];
        else next[k] = v;
      }
      return next;
    }),
  };
}

/** How a command is used (#1214): written, queried, or both. Neither is not offered (every use
 * would fail; the definition checks report it, E-DEF-ACCESS). */
export const COMMAND_ACCESS = ["set", "query", "both"] as const;
export type CommandAccess = (typeof COMMAND_ACCESS)[number];

export function commandAccess(c: CommandSpec): CommandAccess | null {
  const set = c.set !== false;
  const query = c.query !== false;
  return set && query ? "both" : set ? "set" : query ? "query" : null;
}

/** The fields that give a command this use; a default (true) is left unwritten. */
export function accessPatch(access: CommandAccess): CommandSpec {
  return {
    set: access === "query" ? false : undefined,
    query: access === "set" ? false : undefined,
  };
}

export function removeCommand(s: Structure, index: number): Structure {
  return { ...s, commands: s.commands.filter((_, i) => i !== index) };
}

/** A channel command needs `<ch>` in its header to say which channel (unless the definition
 * selects one with a setting); the build area points that out. */
export function needsChannelSuffix(c: CommandSpec): boolean {
  const { target } = commandTarget(c);
  return target.startsWith("ch.") && !(c.header ?? "").includes("<ch>");
}

// ---- rename (#1207) ----------------------------------------------------------------------------

/** A component name as typed: letters, digits, `_` and `-`, no dot (a dot separates the port);
 * null when it is not one or another component has it. */
export function componentName(
  s: Structure,
  raw: string,
  current?: string,
): string | null {
  const name = raw.trim();
  if (!/^[A-Za-z_][A-Za-z0-9_-]*$/.test(name)) return null;
  if (name !== current && names(s).has(name)) return null;
  return name;
}

/** Rename a component and everything that names it: both ends of its wires, the panel ports that
 * lead to it, `refs` parameters of the others, and the commands that run or read it
 * (`<name>.<op>`, `ch.<name>.<op>`). */
export function renameComponent(
  s: Structure,
  scope: Scope,
  from: string,
  to: string,
  docs: ReadonlyMap<string, ComponentDoc> = new Map(),
): Structure {
  if (from === to) return s;
  const wire =
    (list: Scope) =>
    (pair: [string, string]): [string, string] =>
      pair.map((text) => {
        const end = parseEnd(s, list, text);
        return end && end.scope === scope && end.name === from
          ? endText({ ...end, name: to }, list)
          : text;
      }) as [string, string];
  const port = (panel: Scope) =>
    Object.fromEntries(
      Object.entries(s.ports[panel]).map(([p, target]) => {
        const ref = splitRef(target);
        return [
          p,
          panel === scope && ref?.name === from ? `${to}.${ref.port}` : target,
        ];
      }),
    );
  const rename =
    (list: Scope) =>
    (c: StructureComponent): StructureComponent => {
      const own = list === scope && c.name === from ? { ...c, name: to } : c;
      const refs = (docs.get(c.type)?.refs ?? [])
        .filter((r) => r.scope === scope)
        .map((r) => r.param);
      if (!refs.some((k) => (own.params ?? {})[k] === from)) return own;
      return {
        ...own,
        params: Object.fromEntries(
          Object.entries(own.params ?? {}).map(([k, v]) => [
            k,
            refs.includes(k) && v === from ? to : v,
          ]),
        ),
      };
    };
  const prefix = scope === "channel" ? `ch.${from}.` : `${from}.`;
  const renamed = scope === "channel" ? `ch.${to}.` : `${to}.`;
  const command = (c: CommandSpec): CommandSpec => {
    let next = c;
    for (const key of ["op", "read"] as const) {
      const v = next[key];
      if (typeof v === "string" && v.startsWith(prefix))
        next = { ...next, [key]: renamed + v.slice(prefix.length) };
    }
    return next;
  };
  return {
    ...s,
    components: s.components.map(rename("instrument")),
    channel_components: s.channel_components.map(rename("channel")),
    wiring: {
      instrument: s.wiring.instrument.map(wire("instrument")),
      channel: s.wiring.channel.map(wire("channel")),
    },
    ports: { instrument: port("instrument"), channel: port("channel") },
    commands: s.commands.map(command),
  };
}

// ---- undo (#1207) ------------------------------------------------------------------------------

/** The structures behind and ahead of the one shown: an edit pushes the old one behind and clears
 * what was ahead; undo and redo move one step. At most `limit` steps are kept. */
export interface History {
  past: Structure[];
  future: Structure[];
}

export const EMPTY_HISTORY: History = { past: [], future: [] };

export function recordEdit(
  h: History,
  before: Structure,
  limit = 100,
): History {
  return { past: [...h.past, before].slice(-limit), future: [] };
}

export function undo(
  h: History,
  current: Structure,
): { history: History; structure: Structure } | null {
  const prev = h.past.at(-1);
  if (!prev) return null;
  return {
    history: { past: h.past.slice(0, -1), future: [current, ...h.future] },
    structure: prev,
  };
}

export function redo(
  h: History,
  current: Structure,
): { history: History; structure: Structure } | null {
  const next = h.future[0];
  if (!next) return null;
  return {
    history: { past: [...h.past, current], future: h.future.slice(1) },
    structure: next,
  };
}

/** A layout with one node id renamed (a renamed component keeps its place). */
export function renameInLayout(
  layout: Record<string, Point>,
  from: string,
  to: string,
): Record<string, Point> {
  if (!(from in layout)) return layout;
  const out = { ...layout, [to]: layout[from] };
  delete out[from];
  return out;
}
