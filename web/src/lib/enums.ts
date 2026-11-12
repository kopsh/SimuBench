import type { T } from "@/i18n";

/** The member of a closed string list, or undefined: a runtime guard in place of an `as` cast, so a
 * value the backend adds later is shown as it is rather than as a missing translation key (#688). */
export function oneOf<L extends readonly string[]>(
  list: L,
  v: unknown,
): L[number] | undefined {
  return typeof v === "string" && (list as readonly string[]).includes(v)
    ? (v as L[number])
    : undefined;
}

/** The session clocks (``workbench.clockMode.*``); mirrors the API's ``clock`` literal. */
export const CLOCK_MODES = ["command", "wall", "stepped", "scaled"] as const;
export type ClockMode = (typeof CLOCK_MODES)[number];

/** The scope's front-panel actions (``scope.*`` labels). */
export const PANEL_ACTIONS = ["run", "stop", "single"] as const;

/** Who a communication line came from (``panel.client.*`` labels), as the log filters by. */
export const CLIENT_GROUPS = ["script", "agent", "ui", "replay"] as const;
export type ClientGroup = (typeof CLIENT_GROUPS)[number];

/** The group a communication client belongs to; null for a client the log does not know yet. */
export function clientGroup(client: string | null | undefined): ClientGroup | null {
  if (!client) return null;
  if (client === "pyvisa" || client === "sdk") return "script";
  if (client === "mcp") return "agent";
  if (client === "ui" || client === "replay") return client;
  if (
    client.startsWith("socket:") ||
    client.startsWith("hislip:") ||
    client.startsWith("vxi11:") ||
    client.startsWith("bench:")
  ) {
    return "script";
  }
  return null;
}

/** A clock mode in the interface language; an unknown value shows as it is. */
export function clockLabel(v: string, t: T): string {
  const m = oneOf(CLOCK_MODES, v);
  return m ? t(`workbench.clockMode.${m}`) : v;
}

/** A front-panel action in the interface language; an unknown value shows as it is. */
export function panelActionLabel(v: string, t: T): string {
  const a = oneOf(PANEL_ACTIONS, v);
  return a ? t(`scope.${a}`) : v;
}
