import type { Schemas } from "@/api/client";

type ClockMode = Schemas["ClockModeRequest"]["mode"];

/** The rates Live offers, in virtual seconds per real second: 1× is the wall clock (#1660). */
export const LIVE_RATES = [1, 10, 100, 1000] as const;

/** A clock that runs by itself: virtual time follows real time. */
export function isLive(mode: string): boolean {
  return mode === "wall" || mode === "scaled";
}

/** What Live at ``rate`` asks the server for: the wall clock at 1×, a scaled clock otherwise. */
export function liveRequest(rate: number): { mode: ClockMode; scale?: number } {
  return rate === 1 ? { mode: "wall" } : { mode: "scaled", scale: rate };
}

/** The rate a running clock goes at (1 on the wall clock), for the rate picker. */
export function liveRate(
  mode: string,
  scale: number | null | undefined,
): number {
  return mode === "scaled" ? (scale ?? 1) : 1;
}

/**
 * Where Pause goes: back to the clock the session opened on, so a bench meant for the command or
 * the stepped clock returns to it; a bench that opened live pauses onto the command clock, the
 * one a command still moves.
 */
export function pauseMode(startMode: string | null | undefined): ClockMode {
  return startMode === "stepped" ? "stepped" : "command";
}
