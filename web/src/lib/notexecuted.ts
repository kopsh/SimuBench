import type { Event } from "@/api/generated/events";
import type { T } from "@/i18n";

/** The six reasons a command was not executed (tui-design.md 1.1, D18); the engine's
 * classification, shared by the TUI, the Web and MCP. Fault and advance events carry other,
 * kind-specific reasons in the same field, so anything else is shown as it is. The tuple is the
 * key union of ``errors.reasons.*`` as well (issue 639): one list, both jobs. */
const REASONS = [
  "not_modeled",
  "not_in_model",
  "not_in_definition",
  "ignored",
  "builtin_excluded",
  "bad_parameter",
] as const;

/** The reason in the interface language; empty when the event carries none. */
export function reasonText(e: Pick<Event, "reason">, t: T): string {
  if (!e.reason) return "";
  return (REASONS as readonly string[]).includes(e.reason)
    ? t(`errors.reasons.${e.reason as (typeof REASONS)[number]}`)
    : e.reason;
}

/** What the engine adds for a not-executed command: the wording, the parameter with its declared
 * range, the closest command of the author's command space, and the file the declaration is from. */
export function notExecutedSuffix(
  e: Pick<Event, "reason" | "origin" | "closest" | "param" | "range">,
  t: T,
): string {
  const detail = e.param ? `${e.param}${e.range ? ` (${e.range})` : ""}` : "";
  const parts = [reasonText(e, t), detail, e.closest, e.origin].filter(Boolean);
  return parts.length ? `  · ${parts.join(" · ")}` : "";
}
