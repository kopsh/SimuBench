import type {
  CommandSpec,
  CoverageEntryInfo,
  DefinitionChange,
  DefinitionCoverage,
  InstrumentInfo,
  PanelControl,
  ParamSpec,
  StateVarInfo,
} from "@/api/client";
import { pickLabel } from "@/i18n";
import i18n from "@/i18n";
import { displayUnit, formatEng } from "@/lib/format";
import { distinctOptions, formatValue, optionLabel, shortForm } from "@/features/workbench/model";

export type Level = CoverageEntryInfo["level"];

export const LEVELS: Level[] = ["implemented", "accepted", "not_modeled"];

/** Headers compare as the coverage check does: case, spaces, the root colon and a suffix' name ignored. */
export function normHeader(header: string): string {
  return header
    .replace(/<[^>]*>/g, "<>")
    .replace(/\s+/g, "")
    .replace(/^:+/, "")
    .toUpperCase();
}

/** The header's first node, the group the command tree shows ("SENSe", "TRIGger", "*IDN"). */
export function topNode(header: string): string {
  const compact = header.replace(/\s+/g, "");
  if (compact.startsWith("*")) return "*"; // IEEE 488.2 common commands: one group
  const m = compact.match(/^(?:\[:?|:)?([A-Za-z][A-Za-z0-9]*)/);
  return (m?.[1] ?? compact).toUpperCase();
}

export interface CommandRow {
  /** As the definition (or the coverage table) writes it. */
  header: string;
  norm: string;
  level: Level | null;
  note: string | null;
  /** The declarative command, when the definition has it (coverage also lists what it does not). */
  command: CommandSpec | null;
  section: string | null;
}

/**
 * The definition's commands and its coverage table in one list: every modeled command with the
 * coverage level the table gives it, then the manual's other commands (not modeled). Definitions
 * without a coverage table just list their commands.
 */
export function commandRows(commands: CommandSpec[], coverage: DefinitionCoverage | null | undefined): CommandRow[] {
  const rows: CommandRow[] = commands.map((c) => ({
    header: c.header,
    norm: normHeader(c.header),
    level: null,
    note: null,
    command: c,
    section: null,
  }));
  const byHeader = new Map<string, CommandRow[]>();
  for (const row of rows) {
    const list = byHeader.get(row.norm) ?? [];
    list.push(row);
    byHeader.set(row.norm, list);
  }
  for (const entry of coverage?.entries ?? []) {
    const matched = byHeader.get(normHeader(entry.header));
    if (matched) {
      for (const row of matched) {
        row.level = entry.level;
        row.note = entry.note;
        row.section = entry.section;
      }
    } else {
      rows.push({
        header: entry.header,
        norm: normHeader(entry.header),
        level: entry.level,
        note: entry.note,
        command: null,
        section: entry.section,
      });
    }
  }
  return rows;
}

/** The rows by command tree node, both sorted, for the collapsible list. */
export function groupCommands(rows: CommandRow[]): { node: string; rows: CommandRow[] }[] {
  const groups = new Map<string, CommandRow[]>();
  for (const row of rows) {
    const list = groups.get(topNode(row.header)) ?? [];
    list.push(row);
    groups.set(topNode(row.header), list);
  }
  return [...groups.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([node, list]) => ({ node, rows: list.sort((a, b) => a.header.localeCompare(b.header)) }));
}

/** How much of a command group the simulation runs (#1358): accepted counts apart from implemented;
 * a definition without a coverage table counts its commands as implemented. */
export function groupProgress(rows: readonly CommandRow[]): { implemented: number; accepted: number; total: number } {
  let implemented = 0;
  let accepted = 0;
  for (const row of rows) {
    if (row.level === "accepted") accepted++;
    else if (row.level === null || row.level === "implemented") implemented++;
  }
  return { implemented, accepted, total: rows.length };
}

/** A command the simulation runs: one the trial run can send. */
export const runnable = (row: CommandRow) => row.level !== "not_modeled";

/**
 * A command as the trial run's input takes it (#1358): optional nodes left out, a numeric suffix as 1,
 * the query form when it has one; a setting-only command ends in a space for its value.
 */
export function tryForm(row: CommandRow): string {
  let text = "";
  let depth = 0;
  for (const ch of row.header.replaceAll(" ", "")) {
    if (ch === "[") depth++;
    else if (ch === "]") depth = Math.max(0, depth - 1);
    else if (depth === 0) text += ch;
  }
  text = text.replaceAll("<N>", "1").replaceAll("<n>", "1").replaceAll("{n}", "1");
  while (text.includes("<")) text = text.slice(0, text.indexOf("<")) + "1" + text.slice(text.indexOf(">", text.indexOf("<")) + 1);
  if (text.endsWith("?") || !row.command) return text;
  if (row.command.query) return `${text}?`;
  return `${text} `;
}

/** Commands every instrument answers, offered first while the trial console is empty. */
const COMMON = ["*IDN?", "*RST", ":SYSTem:ERRor?"];

/**
 * What the empty trial console offers (#1358): *IDN? and *RST, :SYSTem:ERRor? when the model has it,
 * then the model's own first queries, `max` in all.
 */
export function trySuggestions(rows: readonly CommandRow[], max = 6): string[] {
  const forms = rows.filter(runnable).map(tryForm);
  const has = (c: string) => forms.some((f) => normHeader(f).replace("?", "") === normHeader(c).replace("?", ""));
  const out = COMMON.filter((c) => c.startsWith("*") || has(c));
  for (const f of forms) {
    if (out.length >= max) break;
    if (f.endsWith("?") && !out.includes(f)) out.push(f);
  }
  return out.slice(0, max);
}

/** The acceptance page's verdict line (#1352): command coverage and the cases behind it. */
export interface Verdict {
  /** null: the definition has no coverage table (a contract-written instrument). */
  coverage: { implemented: number; accepted: number; total: number; percent: number } | null;
  cases: { builtinFiles: number; workspaceFiles: number; cases: number };
  /** Of the files run on this page, the passed and failed cases; null until one is run. */
  run: { passed: number; failed: number } | null;
}

export function verdictOf(
  coverage: Pick<DefinitionCoverage, "stats"> | null | undefined,
  files: readonly { path: string; cases: number; builtin?: boolean }[],
  runs: readonly { passed: number; failed: number }[],
): Verdict {
  let cov: Verdict["coverage"] = null;
  if (coverage) {
    const implemented = coverage.stats.implemented ?? 0;
    const accepted = coverage.stats.accepted ?? 0;
    const total = Object.values(coverage.stats).reduce((a, b) => a + (b ?? 0), 0);
    cov = { implemented, accepted, total, percent: total ? Math.round((100 * implemented) / total) : 0 };
  }
  return {
    coverage: cov,
    cases: {
      builtinFiles: files.filter((f) => f.builtin).length,
      workspaceFiles: files.filter((f) => !f.builtin).length,
      cases: files.reduce((n, f) => n + f.cases, 0),
    },
    run: runs.length ? { passed: runs.reduce((n, r) => n + r.passed, 0), failed: runs.reduce((n, r) => n + r.failed, 0) } : null,
  };
}

/** Height of the coverage bar: implemented / accepted / not modeled as percentages. */
export function coverageRatio(coverage: DefinitionCoverage | null | undefined): number {
  const total = coverage ? Object.values(coverage.stats).reduce((a, b) => a + b, 0) : 0;
  return total > 0 ? (coverage!.stats.implemented ?? 0) / total : 0;
}

/** A number in the unit the interface shows; a meter's V and A ranges read 1000 V, not 1 kV. */
export function eng(value: number, unit?: string | null, digits = 6): string {
  const shown = displayUnit(unit);
  if ((shown === "V" || shown === "A") && Math.abs(value) >= 1000) return `${value} ${shown}`;
  return formatEng(value, shown, digits);
}

/** The parameter's shape in one line: type, unit, range or options, optional. */
export function paramFacts(p: ParamSpec): string {
  const facts: string[] = [p.type];
  if (p.unit) facts.push(displayUnit(p.unit));
  if (p.options?.length) facts.push(p.options.map(shortForm).join(" | "));
  if (p.min != null || p.max != null) {
    const lo = p.min != null ? eng(p.min, p.unit, 3) : "−∞";
    const hi = p.max != null ? eng(p.max, p.unit, 3) : "∞";
    facts.push(`${lo} … ${hi}`);
  }
  if (p.optional) facts.push(i18n.t("acceptance.optional"));
  return facts.join(" · ");
}

/** How a command's parameters are bound to settings (what a query reads back). */
export function commandBinding(c: CommandSpec): string | null {
  if (c.state) return c.state;
  if (c.op) return c.op;
  return (c.params ?? []).find((p) => p.state)?.state ?? null;
}

export function stateOf(inst: InstrumentInfo, path: string | null | undefined): StateVarInfo | undefined {
  return path ? inst.state.find((s) => s.path === path) : undefined;
}

export interface ControlView {
  state: string;
  label: string;
  kind: PanelControl["control"];
  /** buttons: the keys' texts; select: the options' texts. */
  choices: string[] | null;
  /** number: the setting's range in its unit. */
  range: string | null;
}

export interface PanelView {
  readouts: { label: string; reading: string; unit: string | null }[];
  groups: { label: string; controls: ControlView[] }[];
}

const BOOL_WORDS = () => [i18n.t("common.on"), i18n.t("common.off")];

/**
 * The definition's front panel in the instrument's language: what it shows and what its groups set.
 * Falls back to the settings themselves when the class has no default panel (a scope, a counter).
 */
export function panelView(inst: InstrumentInfo): PanelView {
  const panel = inst.display.panel;
  const groups0 = panel?.groups ?? [];
  const readouts0 = panel?.readouts ?? [];
  if (!panel || (groups0.length === 0 && readouts0.length === 0)) {
    const groups = new Map<string, ControlView[]>();
    for (const v of inst.state) {
      if (v.readonly) continue;
      const prefix = v.channel != null ? i18n.t("acceptance.groupChannel", { n: v.channel }) : i18n.t("acceptance.groupInstrument");
      const list = groups.get(prefix) ?? [];
      list.push({
        state: v.path,
        label: pickLabel(v.label, v.label_en, v.path),
        kind: v.type === "enum" ? "select" : v.type === "bool" ? "switch" : "number",
        choices: v.type === "enum" ? enumChoices(v) : null,
        range: v.type === "bool" ? null : rangeText(v),
      });
      groups.set(prefix, list);
    }
    return {
      readouts: [],
      groups: [...groups.entries()].map(([label, controls]) => ({ label, controls })),
    };
  }
  return {
    readouts: readouts0.map((r) => ({
      label: pickLabel(r.label, r.label_en, r.reading),
      reading: r.reading,
      unit: r.unit ? displayUnit(r.unit) : null,
    })),
    // A channel's groups (#1315) are listed once, as channel 1's settings, marked as per channel.
    groups: [
      ...groups0.map((g) => ({ g, prefix: "", suffix: "" })),
      ...(panel.channel_groups ?? []).map((g) => ({ g, prefix: "ch1.", suffix: `（${i18n.t("workbench.channels")}）` })),
    ].map(({ g, prefix, suffix }) => ({
      label: pickLabel(g.label, g.label_en, g.label) + suffix,
      controls: (g.controls ?? []).map((c) => {
        const s = stateOf(inst, prefix + c.state);
        const choicesOfSetting = s?.options ? enumChoices(s) : null;
        // Without a control kind the panel uses the setting's own field (#1315).
        const kind = c.control ?? (s?.type === "enum" ? "select" : s?.type === "bool" ? "switch" : "number");
        return {
          state: prefix + c.state,
          label: pickLabel(c.label, c.label_en, s ? pickLabel(s.label, s.label_en, c.state) : c.state),
          kind,
          choices:
            kind === "buttons"
              ? (c.keys ?? []).map((k) => pickLabel(k.label, k.label_en, String(k.value)))
              : kind === "select"
                ? choicesOfSetting
                : null,
          range:
            kind === "number"
              ? c.unit
                ? boundsText(s?.min ?? 0, s?.max ?? 0, (n) => eng(n, c.unit))
                : rangeText(s)
              : null,
        };
      }),
    })),
  };
}

function rangeText(v: StateVarInfo | undefined): string | null {
  if (!v) return null;
  if (v.min != null || v.max != null) {
    // The unit the workbench shows the value in (#1267): a display unit (%, Vpp) first, else the SCPI unit.
    const shown = (n: number) =>
      v.display_unit && v.type === "float"
        ? formatValue({ ...v, value: n, special: null, unset: null }, 3)
        : v.unit
          ? eng(n, v.unit, 3)
          : count(n);
    return boundsText(v.min, v.max, shown);
  }
  if (v.options?.length) return enumChoices(v).join(" | ");
  if (v.type === "bool") return BOOL_WORDS().join(" | ");
  return null;
}

/**
 * An enumeration's choices as the inspector words them (#1357): the name, then the mnemonic a
 * script sends when it differs ("居中 CENT"); an option without a name is its short form alone.
 */
export function enumChoices(v: Pick<StateVarInfo, "options" | "option_labels">): string[] {
  return unique(
    distinctOptions(v).map((o) => {
      const name = optionLabel(v, o);
      const code = shortForm(o);
      return name === code ? code : `${name} ${code}`;
    }),
  );
}

/** A count without a unit: SI prefixed once it is long (2 G points, not 2000000000; #1357). */
function count(n: number): string {
  return Math.abs(n) >= 1e4 ? formatEng(n, "", 6) : String(n);
}

/**
 * A range's two ends (#1357). A definition with no fixed limit writes a symmetric placeholder of a
 * million or more (±1e6 V for a trigger level the vertical scale bounds, ±1e12, ±1e17): that reads
 * as "no fixed limit", not as a number. A missing end is ∓∞, as is one of 1e15 or more.
 */
export function boundsText(min: number | null | undefined, max: number | null | undefined, shown: (n: number) => string): string {
  if (min != null && max != null && max >= 1e6 && min === -max) return i18n.t("acceptance.unbounded");
  const lo = min == null || min <= -1e15 ? "−∞" : shown(min);
  const hi = max == null || max >= 1e15 ? "∞" : shown(max);
  return `${lo} … ${hi}`;
}

/** The technical specifications as rows, a nested record flattened one level ("input: 10 MΩ"). */
export function specRows(specs: Record<string, unknown> | null | undefined): { key: string; value: string }[] {
  const out: { key: string; value: string }[] = [];
  for (const [key, value] of Object.entries(specs ?? {})) {
    if (value && typeof value === "object" && !Array.isArray(value)) {
      for (const [inner, nested] of Object.entries(value as Record<string, unknown>)) {
        out.push({ key: `${key}.${inner}`, value: specValue(nested) });
      }
    } else {
      out.push({ key, value: specValue(value) });
    }
  }
  return out;
}

function specValue(value: unknown): string {
  if (Array.isArray(value)) return value.map(specValue).join("、");
  if (value && typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .map(([k, v]) => `${k}: ${specValue(v)}`)
      .join("；");
  }
  if (typeof value === "number") return String(value);
  if (value === null || value === undefined) return "—";
  return String(value);
}

/** One deviation's limit, as the definition declares it (a number, a table by ranges, or one
 * limit per frequency band). */
export interface OptionRow {
  id: string;
  label: string | null;
  params: string[];
  commands: string[];
}

/** The model's hardware options (D68) as the overview lists them: what each changes. From the
 * definition, so the overview reads without a session; the same rows the session's
 * `InstrumentInfo.options` gives. */
export function optionRows(definition: {
  options?: { id: string; label?: string | null; params?: Record<string, Record<string, unknown>> }[];
  commands?: { header: string; option?: string | null }[];
}): OptionRow[] {
  return (definition.options ?? []).map((o) => ({
    id: o.id,
    label: o.label ?? null,
    params: Object.entries(o.params ?? {}).flatMap(([comp, params]) => Object.keys(params).map((p) => `${comp}.${p}`)),
    commands: (definition.commands ?? []).filter((c) => c.option === o.id).map((c) => c.header),
  }));
}

export function deviationLimit(limit: unknown, unit?: string | null, bands?: number[][] | null): string {
  if (bands?.length) {
    return bands
      .map(([lo, hi], i) => {
        const value = Array.isArray(limit) ? limit[i] : limit;
        return `${eng(lo, "Hz", 3)}–${eng(hi, "Hz", 3)}: ±${typeof value === "number" && unit ? eng(value, unit, 3) : String(value)}`;
      })
      .join("；");
  }
  if (typeof limit === "number") return `±${eng(limit, unit, 3)}`;
  if (limit && typeof limit === "object") {
    return Object.entries(limit as Record<string, unknown>)
      .map(([key, value]) => `${key}: ±${typeof value === "number" ? eng(value, unit, 3) : String(value)}`)
      .join("；");
  }
  return String(limit ?? "—");
}

/** A change item's old/new values as short text. */
export function changeValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (Array.isArray(value)) return value.length ? value.map(changeValue).join("、") : "—";
  if (value && typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .map(([k, v]) => `${k}: ${changeValue(v)}`)
      .join("；");
  }
  if (typeof value === "number") return String(value);
  if (typeof value === "boolean") return value ? i18n.t("common.on") : i18n.t("common.off");
  return String(value);
}

export function changeText(change: DefinitionChange): string {
  return change.detail ? `${change.target}.${change.detail}` : change.target;
}

export function savedAt(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(i18n.language === "en" ? "en-GB" : "zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Labels once each: an option alias shares its label with the option it stands for (#1252). */
function unique(labels: string[]): string[] {
  return [...new Set(labels)];
}
