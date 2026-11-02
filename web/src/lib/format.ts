// Engineering notation for instrument values (ui-design.md 4.3).

const PREFIXES: [number, string][] = [
  [1e12, "T"],
  [1e9, "G"],
  [1e6, "M"],
  [1e3, "k"],
  [1, ""],
  [1e-3, "m"],
  [1e-6, "µ"],
  [1e-9, "n"],
  [1e-12, "p"],
];

/** Unit shown in the UI for a SCPI base unit. */
export function displayUnit(unit?: string | null): string {
  switch ((unit ?? "").toUpperCase()) {
    case "HZ":
      return "Hz";
    case "V":
      return "V";
    case "A":
      return "A";
    case "S":
      return "s";
    case "OHM":
      return "Ω";
    case "DEG":
      return "°";
    case "DBM":
      return "dBm";
    default:
      return unit ?? "";
  }
}

export interface EngParts {
  mantissa: string;
  prefix: string;
  unit: string;
}

export function engParts(value: number, unit = "", digits = 4, fixed = false): EngParts {
  // `fixed` keeps trailing zeros, as an instrument's display shows its digits.
  const shown = (s: string) => (fixed ? s : trim(s));
  if (!Number.isFinite(value)) return { mantissa: value > 0 ? "∞" : value < 0 ? "−∞" : "—", prefix: "", unit };
  if (value === 0 || unit === "°") return { mantissa: shown(value.toPrecision(digits)), prefix: "", unit };
  const mag = Math.abs(value);
  let [scale, prefix] = PREFIXES[PREFIXES.length - 1];
  for (const [s, p] of PREFIXES) {
    if (mag >= s * 0.99995) {
      scale = s;
      prefix = p;
      break;
    }
  }
  return { mantissa: shown((value / scale).toPrecision(digits)), prefix, unit };
}

function trim(s: string): string {
  if (!s.includes(".") || s.includes("e")) return s;
  return s.replace(/0+$/, "").replace(/\.$/, "");
}

export function formatEng(value: number, unit = "", digits = 4): string {
  const p = engParts(value, unit, digits);
  return `${p.mantissa.replace("-", "−")} ${p.prefix}${p.unit}`.trim();
}

// ---- instrument readouts (#278, #313) ----------------------------------------------------------

/**
 * An instrument's overload reading: SCPI instruments answer +9.9E+37 (Keysight DMMs) or
 * +9.91E+37 (the IEEE "not a number" of SMUs); either, or a non-finite value, is overload.
 */
export const OVERLOAD = 9.9e37;

export function isOverload(value: number): boolean {
  return !Number.isFinite(value) || Math.abs(value) >= OVERLOAD * (1 - 1e-9);
}

/** V / I as an SMU's resistance readout computes it: no current is overload, not 0 Ω. */
export function resistance(volts: number, amps: number): number {
  return amps === 0 ? Infinity : volts / amps;
}

export interface ReadoutOptions {
  /** Display unit ("V", "Ω", …); empty for a count. */
  unit?: string;
  /** Significant digits: 6 for a 5½-digit display, 7 for 6½ digits (the leading 1 counts). */
  digits?: number;
  /** Keep trailing zeros, as an instrument's display shows all its digits ("0.00000", not "0"). */
  fixed?: boolean;
  /** Show the sign of a positive value too ("+1.00000"), as a source-measure display does. */
  signed?: boolean;
  /** A count (points, totalize, a channel): a whole number, no prefix. */
  count?: boolean;
}

export interface Readout {
  /** The number as the display shows it; "" when `overload`. */
  mantissa: string;
  prefix: string;
  unit: string;
  /** The value is the instrument's overload: the caller shows its overload word instead. */
  overload: boolean;
}

/** One readout's text: the one place digits, sign, overload and counts are decided (#313). */
export function formatReadout(value: number, options: ReadoutOptions = {}): Readout {
  const { unit = "", digits = 5, fixed = false, signed = false, count = false } = options;
  if (isOverload(value)) return { mantissa: "", prefix: "", unit, overload: true };
  if (count) return { mantissa: String(Math.round(value)), prefix: "", unit, overload: false };
  const p = engParts(value, unit, digits, fixed);
  const mantissa = signed && !p.mantissa.startsWith("-") ? `+${p.mantissa}` : p.mantissa;
  return { mantissa, prefix: p.prefix, unit: p.unit, overload: false };
}

/** Virtual time as hh:mm:ss.mmm (ns input). */
export function formatClock(ns: number): string {
  const totalMs = Math.floor(ns / 1e6);
  const ms = totalMs % 1000;
  const s = Math.floor(totalMs / 1000) % 60;
  const m = Math.floor(totalMs / 60000) % 60;
  const h = Math.floor(totalMs / 3600000);
  const pad = (n: number, w = 2) => String(n).padStart(w, "0");
  return `${pad(h)}:${pad(m)}:${pad(s)}.${pad(ms, 3)}`;
}

const SI: Record<string, number> = { T: 1e12, G: 1e9, M: 1e6, k: 1e3, K: 1e3, m: 1e-3, u: 1e-6, µ: 1e-6, n: 1e-9, p: 1e-12 };

/** Parse user input such as "1.5k", "20 mV", "1e-3" into a number; null when not a number. */
export function parseEngineering(text: string): number | null {
  const t = text.trim().replace(/\s+/g, "").replace("−", "-");
  if (/^[+-]?inf(inity)?$/i.test(t)) return t.startsWith("-") ? -Infinity : Infinity;
  const m = t.match(/^([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)([TGMkKmuµnp])?([a-zA-ZΩ°]*)$/);
  if (!m) return null;
  const base = Number(m[1]);
  return m[2] ? base * SI[m[2]] : base;
}
