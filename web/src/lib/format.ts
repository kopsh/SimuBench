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
    case "DB":
      return "dB";
    case "PCT":
      return "%";
    case "RAD":
      return "rad";
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
  /**
   * The range in use (#1256): the value is shown in the range's prefix with the digits the range
   * leaves (`digits` counts the leading ½ digit), the sign always written, as a meter's display does:
   * "+000.0200" mV on 100 mV at 6½ digits, never a digit beyond the display's resolution.
   */
  range?: number;
  /** The A/D's step on that range: no digit finer than the decade it falls in is shown (#1256). */
  resolution?: number;
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
  if (options.range && Number.isFinite(options.range) && options.range > 0) {
    return { ...rangedParts(value, options.range, digits, options.resolution, unit), unit, overload: false };
  }
  const p = engParts(value, unit, digits, fixed);
  const mantissa = signed && !p.mantissa.startsWith("-") ? `+${p.mantissa}` : p.mantissa;
  return { mantissa, prefix: p.prefix, unit: p.unit, overload: false };
}

/** A reading on a range (#1256): the range's prefix (no kV or kA, a meter has none), the decimals
 * the range leaves at `digits` (the finest step range × 10^-(digits-1), coarsened to the decade of
 * the A/D's `resolution`), the integer part padded to the range's width and the sign written. */
function rangedParts(value: number, range: number, digits: number, resolution: number | undefined, unit: string) {
  const capped = unit === "V" || unit === "A";
  let scale = 1e-12;
  let prefix = "p";
  for (const [s, p] of PREFIXES) {
    if (capped && s > 1) continue;
    if (range >= s * 0.99995) {
      scale = s;
      prefix = p;
      break;
    }
  }
  let step = range * 10 ** -(digits - 1);
  if (resolution && resolution > step) step = 10 ** Math.ceil(Math.log10(resolution) - 1e-9);
  const decimals = Math.max(0, Math.round(-Math.log10(step / scale)));
  const width = Math.max(1, Math.floor(Math.log10(range / scale) + 1e-9) + 1);
  const shown = Math.abs(value / scale).toFixed(decimals);
  const [whole, frac] = shown.split(".");
  const sign = value < 0 && Number(shown) !== 0 ? "-" : "+";
  return { mantissa: `${sign}${whole.padStart(width, "0")}${frac !== undefined ? `.${frac}` : ""}`, prefix };
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

/** The unit word typed after a number, lower case, without its SI prefix: "2 Vrms" → "vrms", "1.5 kHz" → "hz",
 * "20 m" → ""; null when the text is not a number with a unit (#1257). */
export function unitWord(text: string): string | null {
  const t = text.trim().replace(/\s+/g, "").replace("−", "-");
  const m = t.match(/^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?([TGMkKmuµnp])?([a-zA-ZΩ°]*)$/);
  if (!m) return null;
  // "2 m" is 2 milli; "2 MHz" is 2 mega-hertz; a lone prefix letter that is also a unit ("m" metre) is read as the prefix.
  return (m[2] ?? "").toLowerCase();
}

/** Parse user input such as "1.5k", "20 mV", "1e-3" into a number; null when not a number. */
export function parseEngineering(text: string): number | null {
  const t = text.trim().replace(/\s+/g, "").replace("−", "-");
  if (/^[+-]?inf(inity)?$/i.test(t)) return t.startsWith("-") ? -Infinity : Infinity;
  const m = t.match(/^([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)([TGMkKmuµnp])?([a-zA-ZΩ°]*)$/);
  if (!m) return null;
  const base = Number(m[1]);
  return m[2] ? base * SI[m[2]] : base;
}

// ---- makers (#1356) ----------------------------------------------------------------------------

/** Words that only say "a company" and add nothing to a short maker name. */
const MAKER_TAIL = new Set(["technologies", "technology", "inc", "inc.", "co.", "ltd", "ltd."]);

/**
 * A maker's name as lists show it (#1356): the trailing "Technologies" dropped and shouted words
 * written as names ("RIGOL TECHNOLOGIES" → "Rigol", "FLUKE" → "Fluke"); short initialisms ("B&K",
 * "SRS") and mixed-case words ("LeCroy") stay. The full name goes in the title.
 */
export function makerName(manufacturer: string): string {
  const words = manufacturer.trim().split(" ").filter(Boolean);
  while (words.length > 1 && MAKER_TAIL.has(words[words.length - 1].toLowerCase())) words.pop();
  return words
    .map((w) => (w.length > 3 && w === w.toUpperCase() && w !== w.toLowerCase() ? w[0] + w.slice(1).toLowerCase() : w))
    .join(" ");
}
