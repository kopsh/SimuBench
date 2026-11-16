import type { Event } from "@/api/generated/events";
import type { T } from "@/i18n";

/** One line of the clock's "where did the time go" list (#1260): a run of advances with the same cause. */
export interface AdvanceRun {
  reason: string;
  instrument: string | null;
  header: string | null;
  count: number;
  dtNs: number;
  /** Virtual time at the end of the run. */
  tNs: number;
}

/** The latest advances, newest first; consecutive advances with the same cause merge into one run
 * ("scope :DIGitize ×4"), so a burst of acquisitions is one line. */
export function recentAdvances(events: readonly Event[], limit = 6): AdvanceRun[] {
  const runs: AdvanceRun[] = [];
  for (let i = events.length - 1; i >= 0 && runs.length <= limit; i--) {
    const e = events[i];
    if (e.kind !== "advance") continue;
    const reason = e.reason ?? "";
    const instrument = e.instrument ?? null;
    const header = e.header ?? null;
    const last = runs.at(-1);
    if (last && last.reason === reason && last.instrument === instrument && last.header === header) {
      last.count += 1;
      last.dtNs += e.dt_ns ?? 0;
    } else runs.push({ reason, instrument, header, count: 1, dtNs: e.dt_ns ?? 0, tNs: e.t_ns });
  }
  return runs.slice(0, limit);
}

/** Why the clock moved, in words: the command and its instrument, real idle time, a manual advance… */
export function advanceReason(run: { reason: string; instrument: string | null; header: string | null }, t: T): string {
  if (run.reason === "command") return t("workbench.advanceReason.command", { instrument: run.instrument ?? "", header: run.header ?? "" });
  return t(`workbench.advanceReason.${run.reason}`, { defaultValue: run.reason });
}
