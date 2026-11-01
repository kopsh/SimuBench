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
import { shortForm } from "@/features/workbench/model";

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
      const prefix = v.channel != null ? `ch${v.channel}` : "instrument";
      const list = groups.get(prefix) ?? [];
      list.push({
        state: v.path,
        label: pickLabel(v.label, v.label_en, v.path),
        kind: v.type === "enum" ? "select" : v.type === "bool" ? "switch" : "number",
        choices: v.type === "enum" ? (v.options ?? []).map((o) => v.option_labels?.[o] ?? shortForm(o)) : null,
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
    groups: groups0.map((g) => ({
      label: pickLabel(g.label, g.label_en, g.label),
      controls: (g.controls ?? []).map((c) => {
        const s = stateOf(inst, c.state);
        const enumChoices = s?.options?.map((o) => s.option_labels?.[o] ?? shortForm(o)) ?? null;
        return {
          state: c.state,
          label: pickLabel(c.label, c.label_en, s ? pickLabel(s.label, s.label_en, c.state) : c.state),
          kind: c.control,
          choices:
            c.control === "buttons"
              ? (c.keys ?? []).map((k) => pickLabel(k.label, k.label_en, String(k.value)))
              : c.control === "select"
                ? enumChoices
                : null,
          range:
            c.control === "number"
              ? c.unit
                ? `${eng(s?.min ?? 0, c.unit)} … ${eng(s?.max ?? 0, c.unit)}`
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
    const shown = (n: number) => (v.unit ? eng(n, v.unit, 3) : String(n));
    return `${v.min != null ? shown(v.min) : "−∞"} … ${v.max != null ? shown(v.max) : "∞"}`;
  }
  if (v.options?.length) return v.options.map((o) => v.option_labels?.[o] ?? shortForm(o)).join(" | ");
  if (v.type === "bool") return BOOL_WORDS().join(" | ");
  return null;
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

/** One deviation's limit, as the definition declares it (a number or a table by ranges). */
export function deviationLimit(limit: unknown, unit?: string | null): string {
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
