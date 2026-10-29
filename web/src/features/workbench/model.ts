import type { InstrumentInfo, SessionInfo, StateVarInfo } from "@/api/client";
import type { Selection } from "./store";
import { pickLabel } from "@/i18n";
import i18n from "@/i18n";
import { displayUnit, formatEng } from "@/lib/format";

/** SCPI short form of a mixed-case option: SINusoid -> SIN. */
export function shortForm(word: string): string {
  const s = [...word].filter((c) => (c >= "A" && c <= "Z") || (c >= "0" && c <= "9")).join("");
  return s || word.toUpperCase();
}

export function stateMap(inst: InstrumentInfo): Map<string, StateVarInfo> {
  return new Map(inst.state.map((s) => [s.path, s]));
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

export function formatValue(v: StateVarInfo, digits = 4): string {
  const value = v.value;
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
      if (v.display_unit) return `${formatEng(n, "", digits)}${v.display_unit}`;
      return formatEng(n, displayUnit(v.unit), digits);
    }
    default:
      return String(value ?? "");
  }
}

export function instrumentTitle(inst: InstrumentInfo): string {
  return inst.label || inst.name;
}

export function errorsCount(session: SessionInfo): number {
  return session.instruments.reduce((n, i) => n + i.errors.length, 0);
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

