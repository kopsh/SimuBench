/** The SCPI command box's memory (#1342): per instrument, newest first, kept in this browser only. */

export const HISTORY_MAX = 50;
/** Commands that read or clear and change nothing: one click sends them. `*RST` is the title's reset button. */
export const QUICK_COMMANDS = ["*IDN?", "*OPC?", "*CLS", ":SYSTem:ERRor?"] as const;
/** Recent commands shown as chips (they only fill the box: one may be a setting). */
export const RECENT_MAX = 4;

const key = (instrument: string) => `simubench.scpiHistory.${instrument}`;

/** `command` first, any earlier copy dropped, at most `HISTORY_MAX` kept. */
export function pushHistory(history: string[], command: string): string[] {
  return [command, ...history.filter((c) => c !== command)].slice(0, HISTORY_MAX);
}

/** The recent chips: newest first, without what the quick row already offers. */
export function recentChips(history: string[]): string[] {
  const quick = new Set<string>(QUICK_COMMANDS);
  return history.filter((c) => !quick.has(c)).slice(0, RECENT_MAX);
}

export function loadHistory(instrument: string): string[] {
  try {
    const raw = JSON.parse(localStorage.getItem(key(instrument)) ?? "[]");
    return Array.isArray(raw) ? raw.filter((c): c is string => typeof c === "string").slice(0, HISTORY_MAX) : [];
  } catch {
    return [];
  }
}

export function saveHistory(instrument: string, history: string[]): void {
  try {
    localStorage.setItem(key(instrument), JSON.stringify(history));
  } catch {
    // Private window or blocked storage: the history lives for this page only.
  }
}
