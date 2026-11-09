// Motion on instrument screens imitates the instrument (docs/ui-design.md 6): a scope's
// persistence keeps the last acquisitions fading, an analyzer repaints its trace behind a sweep
// line. Both stop under `prefers-reduced-motion`, which also keeps screenshots deterministic.

/** True when the viewer asked for less motion (or the environment cannot tell: no matchMedia). */
export function prefersReducedMotion(): boolean {
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return true;
  }
}

/** How many earlier acquisitions a persistence display keeps, newest first, and their opacity. */
export const PERSISTENCE_ALPHA = [0.3, 0.15, 0.07] as const;

/** The newest-first history after `previous` was replaced by a new acquisition: at most `max`
 * entries; an empty `previous` (nothing was on screen) adds nothing. */
export function pushHistory<T>(history: readonly T[], previous: T | undefined, max = PERSISTENCE_ALPHA.length): T[] {
  if (previous === undefined) return history.slice(0, max);
  return [previous, ...history].slice(0, max);
}

/** How long the screen takes to repaint one sweep, from the instrument's sweep time: never
 * faster than the eye can follow (a 2 ms sweep would be invisible), never slower than the screen
 * refresh, so the line never lags behind the data. */
export function sweepDurationMs(sweepTimeS: number, minMs = 250, maxMs = 1200): number {
  const ms = Number.isFinite(sweepTimeS) && sweepTimeS > 0 ? sweepTimeS * 1000 : minMs;
  return Math.min(maxMs, Math.max(minMs, ms));
}

/** 0..1 through a sweep that started `elapsedMs` ago and lasts `durationMs` (ease-free: a sweep is linear in frequency). */
export function sweepProgress(elapsedMs: number, durationMs: number): number {
  if (!(durationMs > 0)) return 1;
  return Math.min(1, Math.max(0, elapsedMs / durationMs));
}
