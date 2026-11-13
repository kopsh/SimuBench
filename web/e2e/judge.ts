/**
 * How the end-to-end tests judge what the page shows (D49, #926): a value is parsed back and
 * compared with the expected one ± a tolerance, text with plain substrings or equality — never by
 * a regular expression of how it happens to be printed. Playwright's string `name` and
 * `getByText` already match a substring (case-insensitive), `exact: true` the whole text.
 */
import { expect, type Locator, type Page } from "@playwright/test";

/** A bench row's one primary action: "打开测试台" (closed) or "进入" (already open, #281). */
export function openButton(scope: Page | Locator): Locator {
  return scope
    .getByRole("button", { name: "打开测试台", exact: true })
    .or(scope.getByRole("button", { name: "进入", exact: true }));
}

/** The home page's row of the bench file named `name` (its name, not a substring of another). */
export function benchRow(page: Page, name: string): Locator {
  return page.getByRole("listitem").filter({ has: page.getByText(name, { exact: true }) });
}

/** The home page: `/`, whatever the query string. */
export const isHome = (url: URL): boolean => url.pathname === "/";

const HEX = "0123456789abcdef";

/** A workbench URL's session id (`/s/<hex id>`), or null. */
export function sessionIdOf(url: URL | string): string | null {
  const path = new URL(url).pathname;
  if (!path.startsWith("/s/")) return null;
  const id = path.slice("/s/".length);
  return id.length > 0 && [...id].every((c) => HEX.includes(c)) ? id : null;
}

/** A workbench page: `/s/<hex id>`. */
export const isSession = (url: URL): boolean => sessionIdOf(url) !== null;

const PREFIX: Record<string, number> = { p: 1e-12, n: 1e-9, µ: 1e-6, u: 1e-6, m: 1e-3, "": 1, k: 1e3, M: 1e6, G: 1e9 };

/** Whether `text` is a number as JavaScript reads it (`-3.465`, `+5.0005`, `1e-3`). */
function isNumber(text: string): boolean {
  return text.trim() !== "" && Number.isFinite(Number(text));
}

/**
 * The quantity `text` shows, in base units: `"1.25 ms"` → 0.00125 (with `unit` "s"),
 * `"600 µs"` → 6e-4, `"+5.0005 A"` → 5.0005, `"353.55"` → 353.55 (no unit given: the number as
 * shown). The number and the unit may be split by spaces or not (`"200mV"`); null when the text
 * holds no such quantity. The typographic minus the app prints (`"−48.33 mV"`, `formatEng`) reads
 * as a minus.
 */
export function quantity(text: string, unit = ""): number | null {
  const s = text.trim().replaceAll("\u2212", "-");
  let end = s.length;
  while (end > 0 && !isNumber(s.slice(0, end))) end--;
  if (end === 0) return null;
  const value = Number(s.slice(0, end));
  const rest = s.slice(end).trim();
  if (!unit) return value;
  // The unit may carry a per-division tail (`200 mV/div`): it is part of the unit, not the value.
  const suffix = rest.split(" ")[0].split("/")[0];
  if (!suffix.endsWith(unit)) return null;
  const scale = PREFIX[suffix.slice(0, suffix.length - unit.length)];
  return scale === undefined ? null : value * scale;
}

/** Whether a parsed quantity is `center` ± `tol`. */
export const near = (value: number | null, center: number, tol: number): boolean =>
  value !== null && Math.abs(value - center) <= tol;

/** Wait until `read()` passes `ok`. The poll yields the text it read until it passes, so a
 *  failure shows what the page last showed next to what was expected. */
async function eventually(read: () => Promise<string>, ok: (text: string) => boolean, what: string, timeout?: number) {
  const PASSED = { passed: what };
  await expect
    .poll(
      async () => {
        const text = await read();
        return ok(text) ? PASSED : { shown: text, expected: what };
      },
      { message: what, timeout },
    )
    .toEqual(PASSED);
}

const textOf = (loc: Locator) => async () => (await loc.textContent()) ?? "";

/** The locator shows `center` ± `tol` (base units of `unit`; without a unit, the number as shown). */
export async function expectNear(loc: Locator, center: number, tol: number, unit = "", opts: { timeout?: number } = {}) {
  await eventually(
    textOf(loc),
    (text) => {
      return near(quantity(text, unit), center, tol);
    },
    `expected ${center} ± ${tol} ${unit}`.trim(),
    opts.timeout,
  );
}

/** The locator's reading moves out of `center` ± `tol` (it changed from the value held before). */
export async function expectNotNear(loc: Locator, center: number, tol: number, unit = "") {
  await eventually(
    textOf(loc),
    (text) => {
      const v = quantity(text, unit);
      return v !== null && Math.abs(v - center) > tol;
    },
    `expected a reading outside ${center} ± ${tol} ${unit}`.trim(),
  );
}

/** A text field's value passes `ok` (described by `what` in a failure). */
export async function expectValue(loc: Locator, ok: (value: string) => boolean, what: string) {
  await eventually(() => loc.inputValue(), ok, what);
}

/** An element's attribute contains `part`. */
export async function expectAttributeContains(loc: Locator, name: string, part: string) {
  await eventually(async () => (await loc.getAttribute(name)) ?? "", (v) => v.includes(part), `${name} contains ${part}`);
}

/** An element's text passes `ok`. */
export async function expectText(loc: Locator, ok: (text: string) => boolean, what: string) {
  await eventually(textOf(loc), ok, what);
}

/** Whether every character is a decimal digit (and there is at least one). */
export const isDigits = (text: string): boolean => text.length > 0 && [...text].every((c) => c >= "0" && c <= "9");

/** Whether the text holds a decimal digit somewhere. */
export const hasDigit = (text: string): boolean => [...text].some((c) => c >= "0" && c <= "9");

/** A wall-clock time `HH:MM:SS.mmm`. */
export function isClockTime(text: string): boolean {
  const [h, m, rest] = text.trim().split(":");
  const [sec, ms] = (rest ?? "").split(".");
  return h?.length === 2 && isDigits(h) && m?.length === 2 && isDigits(m) && sec?.length === 2 && isDigits(sec) && ms?.length === 3 && isDigits(ms);
}

/** A version that starts `x.y.z` (a pre-release or local tail may follow the patch number). */
export function isVersion(text: string): boolean {
  const [major, minor, patch] = text.trim().split(".");
  return isDigits(major ?? "") && isDigits(minor ?? "") && hasDigit((patch ?? "").slice(0, 1));
}

/**
 * WCAG 2.1 contrast of a text element against what is drawn behind it (#984: the AA check of the
 * internals view). Colors are read back through a canvas, so any CSS color form (the theme's
 * oklch tokens, alpha) arrives as sRGB. The background is the element's ancestors' fills
 * composited from the first opaque one; an SVG label (a wire's) sits on its sibling
 * `.react-flow__edge-textbg` rectangle. Returns the ratio and the AA threshold for the text's
 * size (3 for large text: ≥ 24 px, or ≥ 18.66 px bold; 4.5 otherwise).
 */
export async function contrastOf(loc: Locator): Promise<{ ratio: number; needed: number; text: string }> {
  return loc.evaluate((el) => {
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 1;
    const ctx = canvas.getContext("2d", { willReadFrequently: true })!;
    const rgba = (css: string): [number, number, number, number] => {
      ctx.clearRect(0, 0, 1, 1);
      ctx.fillStyle = "rgba(0, 0, 0, 0)";
      ctx.fillStyle = css;
      ctx.fillRect(0, 0, 1, 1);
      const d = ctx.getImageData(0, 0, 1, 1).data;
      return [d[0], d[1], d[2], d[3] / 255];
    };
    const over = (top: number[], below: number[]) =>
      [0, 1, 2].map((i) => top[i] * top[3] + below[i] * (1 - top[3])).concat(1);
    const fills: number[][] = [];
    const svg = el instanceof SVGElement;
    if (svg) {
      const bg = el.parentElement?.querySelector(".react-flow__edge-textbg");
      if (bg) {
        const s = getComputedStyle(bg);
        const f = rgba(s.fill);
        fills.push([f[0], f[1], f[2], f[3] * Number(s.fillOpacity || 1)]);
      }
    }
    for (let n: Element | null = svg ? el.closest("svg")?.parentElement ?? null : el; n; n = n.parentElement) {
      const f = rgba(getComputedStyle(n).backgroundColor);
      if (f[3] > 0) fills.push(f);
      if (f[3] >= 1) break;
    }
    let bg = [255, 255, 255, 1];
    for (const f of fills.reverse()) bg = over(f, bg);
    const style = getComputedStyle(el);
    const fg = over(rgba(svg ? style.fill : style.color), bg);
    const lum = (c: number[]) => {
      const [r, g, b] = c.slice(0, 3).map((v) => {
        const s = v / 255;
        return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    };
    const [hi, lo] = [lum(fg), lum(bg)].sort((a, b) => b - a);
    const size = parseFloat(style.fontSize);
    const bold = Number(style.fontWeight) >= 700;
    return { ratio: (hi + 0.05) / (lo + 0.05), needed: size >= 24 || (bold && size >= 18.66) ? 3 : 4.5, text: el.textContent ?? "" };
  });
}

/** Every element `loc` matches meets WCAG AA contrast (`contrastOf`); the failures are named. */
export async function expectContrastAA(loc: Locator) {
  const count = await loc.count();
  expect(count).toBeGreaterThan(0);
  const failures: string[] = [];
  for (let i = 0; i < count; i++) {
    const { ratio, needed, text } = await contrastOf(loc.nth(i));
    if (ratio < needed) failures.push(`"${text.trim().slice(0, 40)}" ${ratio.toFixed(2)} < ${needed}`);
  }
  expect(failures).toEqual([]);
}
