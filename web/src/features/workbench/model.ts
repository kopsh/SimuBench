import type { InstrumentInfo, SessionInfo, StateVarInfo } from "@/api/client";
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
}

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
  const all = [...groups.values()];
  const named = all.filter((g) => !g.remote && g.id !== OTHER);
  const other = all.filter((g) => g.id === OTHER);
  return { main, groups: [...named, ...other, ...all.filter((g) => g.remote)] };
}

/** The group of flat settings the definition did not name. */
export const OTHER = "other";

/**
 * Whether a group starts open. A named setting is one the definition's author means as a knob (a
 * multimeter's aperture, a source's autorange), so a group of those is open; the folded ones are the
 * remote-programming groups and groups the definition did not name (a template's measurement slots).
 */
/**
 * The name of a settings group (``inspector.group.<id>``); a numbered prefix (``function3``) takes
 * ``<prefix>N`` with the number. None when the translation files have no name for it: every prefix
 * of the built-in models has one (``tests/test_setting_groups.py``, #1251).
 */
export function groupName(id: string): string | null {
  let digits = id.length;
  while (digits > 0 && id[digits - 1] >= "0" && id[digits - 1] <= "9") digits--;
  if (digits > 0 && digits < id.length && i18n.exists(`inspector.group.${id.slice(0, digits)}N`)) {
    return i18n.t(`inspector.group.${id.slice(0, digits)}N` as never, { n: id.slice(digits) });
  }
  return i18n.exists(`inspector.group.${id}`) ? i18n.t(`inspector.group.${id}` as never) : null;
}

export function groupOpen(g: SettingGroup): boolean {
  return !g.remote && g.vars.filter(hasLabel).length * 2 >= g.vars.length;
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
