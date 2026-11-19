import type { InstrumentInfo, PanelGroup, SessionInfo, StateVarInfo } from "@/api/client";
import type { Selection } from "./store";
import { pickLabel } from "@/i18n";
import i18n from "@/i18n";
import optionWords from "@/i18n/option-words.json";
import { displayUnit, formatEng } from "@/lib/format";

/** SCPI short form of a mixed-case option: SINusoid -> SIN. */
export function shortForm(word: string): string {
  const s = [...word].filter((c) => (c >= "A" && c <= "Z") || (c >= "0" && c <= "9")).join("");
  return s || word.toUpperCase();
}

/**
 * The words SCPI settings commonly take, in both languages (#281, #1257): a setting without its own
 * option_labels shows "正弦 SIN", not the raw mnemonic. Keyed by the option as written ("COMMa"),
 * by its short form ("SIN"), or by a numbered family ("CHAN#" → 通道 3). The list is shared with
 * tests/test_option_words.py, which fails when a built-in model's option has no Chinese name.
 */
const WORDS = optionWords as unknown as Record<string, [string, string]>;

/** The option's word pair from the shared list, or null: exported beside ``optionLabel`` because
 * the Python test's ``word_of`` must agree with it case for case (#1286). */
export function wordOf(option: string): [string, string] | null {
  const direct = WORDS[option] ?? WORDS[shortForm(option)];
  if (direct) return direct;
  const short = shortForm(option);
  let cut = short.length;
  while (cut > 0 && short[cut - 1] >= "0" && short[cut - 1] <= "9") cut--;
  const family = cut > 0 && cut < short.length ? WORDS[`${short.slice(0, cut)}#`] : undefined;
  if (!family) return null;
  const n = short.slice(cut);
  return [family[0].replace("{n}", n), family[1].replace("{n}", n)];
}

/**
 * The options worth offering: an alias the instrument also accepts (UXR ``DCFifty`` beside ``DC50``,
 * one label for both) would show as the same word twice, so the second of a label is left out —
 * unless it is the current value, which a select must still be able to show (#1252).
 */
/**
 * The bottom tab the log opens on by itself (#1360): the first that has something to read —
 * communication, then events, then problems — so it does not open on an empty table.
 */
export function autoBottomTab(has: { comms: boolean; events: boolean; problems: boolean }): "comms" | "events" | "problems" {
  if (has.comms) return "comms";
  if (has.events) return "events";
  if (has.problems) return "problems";
  return "comms";
}

export function distinctOptions(v: Pick<StateVarInfo, "options" | "option_labels"> & { value?: unknown }): string[] {
  const seen = new Set<string>();
  return (v.options ?? []).filter((o) => {
    const label = optionLabel(v, o);
    if (seen.has(label) && o !== String(v.value)) return false;
    seen.add(label);
    return true;
  });
}

/** A setting's option as the panel shows it: its own label, a common word, or the short form. */
export function optionLabel(v: Pick<StateVarInfo, "option_labels">, option: string): string {
  const own = v.option_labels?.[option];
  if (own) return own;
  const word = wordOf(option);
  if (word) return i18n.language === "en" ? word[1] : word[0];
  return shortForm(option);
}

export function stateMap(inst: InstrumentInfo): Map<string, StateVarInfo> {
  return new Map(inst.state.map((s) => [s.path, s]));
}

/**
 * A manual setting whose automatic switch is on (``bw.res`` beside ``bw.res_auto``): the stored
 * value is not the one in effect, so it is shown dimmed (docs/ui-design.md, #279).
 */
export function inactiveUnderAuto(vars: Map<string, StateVarInfo>, path: string): boolean {
  const auto = vars.get(`${path}_auto`);
  return auto?.type === "bool" && auto.value === true;
}

export function channelVars(inst: InstrumentInfo, channel: number): StateVarInfo[] {
  return inst.state.filter((s) => s.channel === channel);
}

export function globalVars(inst: InstrumentInfo): StateVarInfo[] {
  return inst.state.filter((s) => s.channel == null);
}

export function varName(v: StateVarInfo): string {
  return v.channel != null ? v.path.slice(v.path.indexOf(".") + 1) : v.path;
}

export function labelOf(v: StateVarInfo): string {
  return pickLabel(v.label, v.label_en, varName(v));
}

/** Numbers come as numbers, except infinities which the service sends as "INF". */
export function numeric(value: unknown): number {
  if (value === "INF") return Infinity;
  if (value === "-INF") return -Infinity;
  if (typeof value === "number") return value;
  return Number(value);
}

/**
 * A number that stands for "automatic" or "not given" (#1257): the definition's special AUTO value
 * (a range of 0 meaning AUTO) or its ``unset`` value (a resolution CONFigure left out). Shown as the
 * word, never as "0 V", which reads as a range of 0 V.
 */
export function isAutoValue(v: Pick<StateVarInfo, "special" | "unset" | "value" | "type">): boolean {
  if (v.type !== "float" && v.type !== "int") return false;
  const n = numeric(v.value);
  if (v.unset != null && n === v.unset) return true;
  return Object.entries(v.special ?? {}).some(([word, value]) => word.toUpperCase().startsWith("AUTO") && value === n);
}

export function formatValue(v: StateVarInfo, digits = 4): string {
  const value = v.value;
  if (isAutoValue(v)) return i18n.t("workbench.auto");
  switch (v.type) {
    case "bool":
      return value ? i18n.t("common.on") : i18n.t("common.off");
    case "enum":
      return v.option_labels?.[String(value)] ?? shortForm(String(value));
    case "int":
      return String(numeric(value));
    case "float": {
      const n = numeric(value);
      if (!Number.isFinite(n) && v.unit?.toUpperCase() === "OHM") return i18n.t("generator.highZ");
      // A unit word ("Vpp") takes the prefix like a unit ("500 mVpp"); a sign ("%") follows the plain
      // number, with no prefix ("0.001%", not "1 m%", #1267).
      if (v.display_unit) {
        const word = [...v.display_unit].every((c) => c.toLowerCase() !== c.toUpperCase());
        if (word) return formatEng(n, v.display_unit, digits);
        return `${String(Number(n.toPrecision(Math.max(digits, 6)))).replace("-", "−")}${v.display_unit}`;
      }
      return formatEng(n, displayUnit(v.unit), digits);
    }
    default:
      return String(value ?? "");
  }
}

export function instrumentTitle(inst: InstrumentInfo): string {
  return inst.label || inst.name;
}

/**
 * A net's name in the resource tree (#1222): the instrument and part ports it joins, not its id
 * ("net-1") — what a probe on it would be clipped to. Points inside a wire's accessories (a probe's
 * ends, "wire1.passive_probe0.IN") are left out; a net with nothing else keeps its id.
 */
export function netTitle(members: string[], owners: Iterable<string>): string {
  const known = new Set(owners);
  const ports = members.filter((m) => known.has(m.split(".")[0]));
  return ports.length ? ports.join(" · ") : "";
}

export function errorsCount(session: SessionInfo): number {
  return session.instruments.reduce((n, i) => n + i.errors.length + (i.warnings?.length ?? 0), 0); // warnings too (#1264)
}

/** The instrument port a wire end refers to ('awg.OUT1'), if it is one. */
function instrumentPortOfRef(session: SessionInfo, ref: string) {
  const dot = ref.lastIndexOf(".");
  const inst = session.instruments.find((i) => i.name === ref.slice(0, dot));
  return inst?.ports.find((p) => p.name === ref.slice(dot + 1));
}

/** Channel of the instrument port a wire end refers to, for coloring. */
export function channelOfRef(session: SessionInfo, ref: string): number | null {
  return instrumentPortOfRef(session, ref)?.channel ?? null;
}

/** Whether a wire end is a reference port (a timebase or a lock-in reference). */
export function isReferenceRef(session: SessionInfo, ref: string): boolean {
  return instrumentPortOfRef(session, ref)?.medium === "reference";
}

export function accessoryLabel(type: string, params: Record<string, unknown>): string {
  if (type === "passive_probe") return `${numeric(params.attenuation ?? 10)}×`;
  if (type === "coax_cable") return "coax";
  if (type === "lead") return `${numeric(params.r ?? 0.1)} Ω`;
  return type;
}

/** What a reference in a traced path ('awg.OUT1', 'rc', 'wire1.passive_probe0') is called, and what selecting it selects. */
export function refTarget(session: SessionInfo, ref: string): { label: string; selection: Selection | null } {
  const acc = /^wire(\d+)\.([a-z_]+?)(\d+)$/.exec(ref);
  if (acc) {
    const wire = session.wires.find((w) => w.index === Number(acc[1]));
    const a = wire?.accessories[Number(acc[3])];
    return {
      label: a?.type === "passive_probe" ? i18n.t("scope.silence.probe", { label: accessoryLabel(a.type, a.params) }) : a ? accessoryLabel(a.type, a.params) : acc[2],
      selection: wire ? { kind: "wire", id: wire.index } : null,
    };
  }
  const part = session.parts.find((d) => d.name === ref);
  if (part) return { label: part.label || part.name, selection: { kind: "part", id: part.name } };
  const dot = ref.lastIndexOf(".");
  const inst = session.instruments.find((i) => i.name === (dot > 0 ? ref.slice(0, dot) : ref));
  if (inst) {
    const port = dot > 0 ? ` ${ref.slice(dot + 1)}` : "";
    return { label: `${instrumentTitle(inst)}${port}`, selection: { kind: "instrument", id: inst.name } };
  }
  return { label: ref, selection: null };
}


/* ---- The inspector's settings, grouped (#728) ---------------------------------------------------
 * An instrument carries every setting its SCPI commands bind to, a real front panel shows a few.
 * The definition's own summary lists are the main tier; the rest group by the first path segment
 * and stay folded, the remote-programming ones last. No definition format change: a template is
 * expanded once (decisions.md D3: no later upgrade), so the grouping has to cope with unlabelled settings. */

/** Path prefixes of settings that only exist for remote programming. */
const REMOTE_SEGMENTS = new Set(["comm", "waveform", "status", "system", "remote", "interface", "io", "lan", "gpib", "usb", "visa", "hislip", "format"]);

/** True when the definition gave the setting a name of its own (else the path is all there is). */
export function hasLabel(v: Pick<StateVarInfo, "label" | "label_en">): boolean {
  return !!(v.label || v.label_en);
}

export interface SettingGroup {
  /** First path segment: the group's key (and its i18n name, ``inspector.group.<id>``). */
  id: string;
  /** Remote-programming settings, listed after the others. */
  remote: boolean;
  vars: StateVarInfo[];
  /**
   * Numbered groups of one prefix (``function1`` … ``function16``) joined into one (#1299): the
   * instance numbers in order, each with its settings; shown one at a time with a picker. The id
   * is then the prefix (``function``).
   */
  numbered?: InstanceFamily;
}

/** At least this many numbered groups of one prefix are joined into one (as instance families, #1257). */
const NUMBERED_MIN = 4;

export interface SplitSettings {
  /** What a front panel puts first: the definition's summary lists, then the flat settings. */
  main: StateVarInfo[];
  groups: SettingGroup[];
}

/** Segment of a setting that groups it: ``measure.p1.kind`` -> ``measure``; none for ``scale``. */
function groupSegment(name: string): string | null {
  const dot = name.indexOf(".");
  return dot > 0 ? name.slice(0, dot) : null;
}

/**
 * Split an instrument's settings (all global ones, or one channel's) into the main tier and folded
 * groups. ``summaryKeys`` are the definition's summary: full paths for the instrument, names
 * without the channel prefix for a channel.
 */
export function splitSettings(vars: StateVarInfo[], summaryKeys: string[]): SplitSettings {
  const nameOf = (v: StateVarInfo) => varName(v);
  const byName = new Map(vars.map((v) => [nameOf(v), v]));
  const main: StateVarInfo[] = [];
  const seen = new Set<string>();
  for (const key of summaryKeys) {
    const v = byName.get(key);
    if (v && !seen.has(key)) {
      main.push(v);
      seen.add(key);
    }
  }
  const groups = new Map<string, SettingGroup>();
  const group = (id: string) => {
    let g = groups.get(id);
    if (!g) {
      g = { id, remote: REMOTE_SEGMENTS.has(id), vars: [] };
      groups.set(id, g);
    }
    return g;
  };
  for (const v of vars) {
    const name = nameOf(v);
    if (seen.has(name)) continue;
    const seg = groupSegment(name);
    if (seg === null) {
      // A flat setting is a top-level knob, but only one the definition named: an unnamed one would
      // head the panel under its raw path, so it waits in "other".
      (hasLabel(v) ? main : group(OTHER).vars).push(v);
      continue;
    }
    group(seg).vars.push(v);
  }
  const all = joinNumbered([...groups.values()]);
  const named = all.filter((g) => !g.remote && g.id !== OTHER);
  const other = all.filter((g) => g.id === OTHER);
  return { main, groups: [...named, ...other, ...all.filter((g) => g.remote)] };
}

/* ---- The panel and the rest (#1313) --------------------------------------------------------------
 * The inspector is not the instrument's whole menu: a scope binds over a thousand settings to its
 * SCPI commands (UXR1104B: 1861), and listing them, even folded, made the panel a long scroll whose
 * every control slowed the next dropdown. The panel shows what a front panel puts first; the rest
 * is reached by name (``findSettings``) or under 「全部设置」, which is mounted only when opened. */

/**
 * What the panel shows of these settings: the definition's summary, exactly, when it gives one
 * (the named flat settings beside it wait in the rest); without a summary, the named flat settings.
 */
export function panelSettings(vars: StateVarInfo[], summaryKeys: string[]): { panel: StateVarInfo[]; rest: StateVarInfo[] } {
  const { main } = splitSettings(vars, summaryKeys);
  const keys = new Set(summaryKeys);
  const summarized = main.filter((v) => keys.has(varName(v)));
  const panel = summarized.length > 0 ? summarized : main;
  const shown = new Set(panel);
  return { panel, rest: vars.filter((v) => !shown.has(v)) };
}

/** A setting's value and a declared value are the same: numbers by value ("INF" included), others exactly. */
export function sameValue(a: unknown, b: unknown): boolean {
  if (typeof a === "boolean" || typeof b === "boolean") return a === b;
  if ((typeof a === "number" || typeof b === "number") && a != null && b != null) return numeric(a) === numeric(b);
  return a === b;
}

/**
 * Whether a panel ``when`` holds (#1315): every named setting has the value, or one of the values of
 * a list. In a channel's group (``channel`` given) a name that is no whole-instrument setting is that
 * channel's (``scale`` → ``ch2.scale``).
 */
export function whenHolds(when: Record<string, unknown> | undefined, vars: Map<string, StateVarInfo>, channel?: number): boolean {
  return Object.entries(when ?? {}).every(([path, want]) => {
    const v = vars.get(path) ?? (channel != null ? vars.get(`ch${channel}.${path}`) : undefined);
    if (!v) return false;
    return Array.isArray(want) ? want.some((w) => sameValue(v.value, w)) : sameValue(v.value, want);
  });
}

export interface PanelSection {
  label: string;
  label_en?: string | null;
  vars: StateVarInfo[];
}

/**
 * The declared panel's groups as they stand now (#1315, D74): groups and controls whose ``when``
 * holds, each control's setting (a channel group's for ``channel``), minus what ``keep`` leaves out
 * (shown on the screen beside); groups left empty are dropped.
 */
export function panelSections(
  groups: PanelGroup[] | undefined,
  vars: Map<string, StateVarInfo>,
  keep: (v: StateVarInfo) => boolean,
  channel?: number,
): PanelSection[] {
  const out: PanelSection[] = [];
  for (const g of groups ?? []) {
    if (!whenHolds(g.when, vars, channel)) continue;
    const shown = (g.controls ?? [])
      .filter((c) => whenHolds(c.when, vars, channel))
      .map((c) => vars.get(channel != null ? `ch${channel}.${c.state}` : c.state))
      .filter((v): v is StateVarInfo => !!v && keep(v));
    if (shown.length) out.push({ label: g.label, label_en: g.label_en, vars: shown });
  }
  return out;
}

/** 「全部设置」 starts open when it holds no more than this many settings: a small instrument keeps them in sight. */
export const ALL_OPEN_MAX = 40;

/** The most settings a search lists at once; more matches ask for more words. */
export const FIND_MAX = 30;

/**
 * Settings whose name (Chinese or English) or path holds every word of ``query``, ignoring case,
 * in the given order; at most ``limit`` of them, with the number that matched.
 */
export function findSettings(vars: StateVarInfo[], query: string, limit = FIND_MAX): { hits: StateVarInfo[]; total: number } {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return { hits: [], total: 0 };
  const matched = vars.filter((v) => {
    const text = `${v.label ?? ""} ${v.label_en ?? ""} ${v.path}`.toLowerCase();
    return words.every((w) => text.includes(w));
  });
  return { hits: matched.slice(0, limit), total: matched.length };
}

/** ``function12`` -> ``["function", "12"]``; null without a letter prefix or a number. */
function numberedId(id: string): [string, string] | null {
  let cut = id.length;
  while (cut > 0 && id[cut - 1] >= "0" && id[cut - 1] <= "9") cut--;
  return cut > 0 && cut < id.length ? [id.slice(0, cut), id.slice(cut)] : null;
}

/** Numbered groups of one prefix, when there are enough of them, become one group in the first one's place. */
function joinNumbered(groups: SettingGroup[]): SettingGroup[] {
  const byPrefix = new Map<string, SettingGroup[]>();
  for (const g of groups) {
    const n = numberedId(g.id);
    if (n) byPrefix.set(n[0], [...(byPrefix.get(n[0]) ?? []), g]);
  }
  const out: SettingGroup[] = [];
  for (const g of groups) {
    const n = numberedId(g.id);
    const same = n ? byPrefix.get(n[0])! : [];
    if (!n || same.length < NUMBERED_MIN) {
      out.push(g);
      continue;
    }
    if (same[0] !== g) continue; // joined into the first
    const ordered = [...same].sort((a, b) => Number(numberedId(a.id)![1]) - Number(numberedId(b.id)![1]));
    const instances = ordered.map((x) => numberedId(x.id)![1]);
    out.push({
      id: n[0],
      remote: g.remote,
      vars: ordered.flatMap((x) => x.vars),
      numbered: { key: n[0], name: n[0], instances, byInstance: new Map(ordered.map((x, i) => [instances[i], x.vars])) },
    });
  }
  return out;
}

/** The group of flat settings the definition did not name. */
export const OTHER = "other";

/**
 * The name of a settings group (``inspector.group.<id>``); a numbered prefix (``function3``) takes
 * ``<prefix>N`` with the number. None when the translation files have no name for it: every prefix
 * of the built-in models has one (``tests/test_setting_groups.py``, #1251).
 */
export function groupName(id: string, numbered = false): string | null {
  // A joined numbered group (#1299) takes its prefix's numbered name without a number: "数学函数",
  // not the "function" group's "功能" (a generator's function settings).
  if (numbered && i18n.exists(`inspector.group.${id}N`)) return String(i18n.t(`inspector.group.${id}N` as never, { n: "" })).trim();
  const n = numberedId(id);
  if (n && i18n.exists(`inspector.group.${n[0]}N`)) return i18n.t(`inspector.group.${n[0]}N` as never, { n: n[1] });
  return i18n.exists(`inspector.group.${id}`) ? i18n.t(`inspector.group.${id}` as never) : null;
}

/** A group with more rows than this starts folded, however well named (#1299): it would push the rest away. */
export const OPEN_ROWS_MAX = 12;

/** The rows a group shows: its settings, with a measurement slot or an instance family as one row each. */
export function groupRows(g: SettingGroup): number {
  if (g.numbered) return 1 + (g.numbered.byInstance.get(g.numbered.instances[0])?.length ?? 0);
  const { slots, rest } = slotRows(g.vars);
  const { families, rest: flat } = instanceFamilies(rest);
  return slots.length + families.length + flat.length;
}

/**
 * Whether a group starts open. A named setting is one the definition's author means as a knob (a
 * multimeter's aperture, a source's autorange), so a short group of those is open; the folded ones
 * are the remote-programming groups, groups the definition did not name (a template's measurement
 * slots), joined numbered groups and long groups (#1299: a scope's sixteen math functions open at
 * once mounted hundreds of fields on a click).
 */
export function groupOpen(g: SettingGroup): boolean {
  return !g.remote && !g.numbered && g.vars.filter(hasLabel).length * 2 >= g.vars.length && groupRows(g) <= OPEN_ROWS_MAX;
}

export interface SlotRow {
  /** ``P1``: the slot's name. */
  label: string;
  vars: StateVarInfo[];
}

/**
 * ``measure.p1.kind`` and ``measure.p1.source`` are one measurement slot: one row of two controls
 * instead of two rows each, eight slots in all. Settings that are not slots stay in ``rest``.
 */
export function slotRows(vars: StateVarInfo[]): { slots: SlotRow[]; rest: StateVarInfo[] } {
  const slots = new Map<string, SlotRow>();
  const rest: StateVarInfo[] = [];
  for (const v of vars) {
    const m = /^[^.]+\.(p\d+)\.[^.]+$/.exec(varName(v));
    if (!m) {
      rest.push(v);
      continue;
    }
    const row = slots.get(m[1]) ?? { label: m[1].toUpperCase(), vars: [] };
    row.vars.push(v);
    slots.set(m[1], row);
  }
  return { slots: [...slots.values()], rest };
}

export interface InstanceFamily {
  /** ``display.bookmark``: the settings' path up to the numbered segment's letters. */
  key: string;
  /** The numbered segment's letters, ``bookmark``. */
  name: string;
  /** The instance numbers, in order. */
  instances: string[];
  byInstance: Map<string, StateVarInfo[]>;
}

/**
 * Settings a definition declares once with ``each`` (``display.bookmark{n}.color`` for n = 1…100)
 * come in families of numbered instances (#1257): listed flat they are hundreds of rows. A family of
 * at least ``min`` instances is shown as one block with an instance picker; the rest stay as rows.
 */
export function instanceFamilies(vars: StateVarInfo[], min = 4): { families: InstanceFamily[]; rest: StateVarInfo[] } {
  const found = new Map<string, InstanceFamily>();
  const where = new Map<StateVarInfo, string>();
  for (const v of vars) {
    const segs = varName(v).split(".");
    for (let i = 1; i < segs.length - 1; i++) {
      const seg = segs[i];
      let cut = seg.length;
      while (cut > 0 && seg[cut - 1] >= "0" && seg[cut - 1] <= "9") cut--;
      if (cut === 0 || cut === seg.length) continue;
      const name = seg.slice(0, cut);
      const key = [...segs.slice(0, i), name].join(".");
      const n = seg.slice(cut);
      const fam = found.get(key) ?? { key, name, instances: [], byInstance: new Map<string, StateVarInfo[]>() };
      if (!fam.byInstance.has(n)) {
        fam.byInstance.set(n, []);
        fam.instances.push(n);
      }
      fam.byInstance.get(n)!.push(v);
      found.set(key, fam);
      where.set(v, key);
      break;
    }
  }
  const families = [...found.values()].filter((f) => f.instances.length >= min);
  const folded = new Set(families.map((f) => f.key));
  for (const f of families) f.instances.sort((a, b) => Number(a) - Number(b));
  return { families, rest: vars.filter((v) => !folded.has(where.get(v) ?? "")) };
}

/* ---- Stepping a number up and down (#738) -----------------------------------------------------
 * A real instrument's knob moves a value one rung at a time. The rung comes from the setting itself:
 * its ladder (1-2-5, 1-3 or a list) when it has one, a whole count for an integer, else a tenth of the
 * decade the value is in (1.5 kHz moves 100 Hz). No definition field is added. */

type Steppable = Pick<StateVarInfo, "type" | "min" | "max" | "ladder">;

const RUNGS: Record<string, number[]> = { "1-2-5": [1, 2, 5], "1-3": [1, 3] };

/** The next rung of a ladder above (``dir`` 1) or below (-1) a positive ``value``; none outside a list. */
function ladderStep(ladder: string | number[], value: number, dir: 1 | -1): number | null {
  const tol = 1e-9;
  if (Array.isArray(ladder)) {
    const sorted = [...ladder].sort((a, b) => a - b);
    return (dir > 0 ? sorted.find((r) => r > value * (1 + tol)) : [...sorted].reverse().find((r) => r < value * (1 - tol))) ?? null;
  }
  const mantissas = RUNGS[ladder];
  if (!mantissas || !(value > 0)) return null;
  const decade = Math.floor(Math.log10(value * (dir > 0 ? 1 + tol : 1 - tol)));
  const rungs = [decade - 1, decade, decade + 1].flatMap((e) => mantissas.map((m) => Number((m * 10 ** e).toPrecision(12))));
  return (dir > 0 ? rungs.find((r) => r > value * (1 + tol)) : [...rungs].reverse().find((r) => r < value * (1 - tol))) ?? null;
}

/**
 * The value one step up (``dir`` 1) or down (-1) from ``value``, or null when there is nowhere to
 * go: no finite number, or already at the limit. ``coarse`` (Shift) moves ten times as far, or a
 * whole decade of a ladder.
 */
export function stepNumber(spec: Steppable, value: number, dir: 1 | -1, coarse = false): number | null {
  if (!Number.isFinite(value)) return null;
  let next: number | null = null;
  if (spec.ladder && value > 0) {
    let at: number | null = value;
    const rungs = coarse ? (Array.isArray(spec.ladder) ? 3 : (RUNGS[spec.ladder]?.length ?? 1)) : 1;
    for (let i = 0; i < rungs && at !== null; i++) {
      const stepped = ladderStep(spec.ladder, at, dir);
      if (stepped === null) break;
      at = stepped;
    }
    next = at === value ? null : at;
  }
  if (next === null && !spec.ladder) {
    let step: number;
    if (spec.type === "int") step = 1;
    else if (value !== 0) step = 10 ** Math.floor(Math.log10(Math.abs(value) * (dir < 0 ? 1 - 1e-9 : 1))) / 10;
    else step = spec.min != null && spec.max != null && spec.max > spec.min ? (spec.max - spec.min) / 100 : 1;
    if (coarse) step *= 10;
    next = Number((value + dir * step).toPrecision(12));
  }
  if (next === null) return null;
  if (spec.min != null) next = Math.max(spec.min, next);
  if (spec.max != null) next = Math.min(spec.max, next);
  return next === value ? null : next;
}
