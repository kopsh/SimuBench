import { createContext, useContext, useEffect, useMemo } from "react";
import { create } from "zustand";

/**
 * The settings a screen shows as fields (#1229). Every StateField rendered inside a screen tab
 * counts its path here while it is mounted, so the inspector beside that screen leaves those
 * settings out instead of offering the same value in two places. Counted, not read from a list per
 * class: a definition's own panel and the generic layout show different fields for each model.
 */
export const OnScreen = createContext(false);

const key = (instrument: string, path: string) => `${instrument}\n${path}`;

export const useScreenFields = create<{ counts: Record<string, number>; add: (instrument: string, path: string) => () => void }>(
  (set) => ({
    counts: {},
    add: (instrument, path) => {
      const k = key(instrument, path);
      set((s) => ({ counts: { ...s.counts, [k]: (s.counts[k] ?? 0) + 1 } }));
      return () =>
        set((s) => {
          const counts = { ...s.counts };
          if ((counts[k] ?? 0) <= 1) delete counts[k];
          else counts[k] -= 1;
          return { counts };
        });
    },
  }),
);

/** Count this field as shown on a screen while it is mounted inside one (and ``counted``). */
export function useCountOnScreen(instrument: string, path: string, counted = true) {
  const onScreen = useContext(OnScreen) && counted;
  useEffect(() => (onScreen ? useScreenFields.getState().add(instrument, path) : undefined), [onScreen, instrument, path]);
}

/** The paths of `instrument` that its screen shows as fields; empty when `instrument` is null. */
export function useScreenPaths(instrument: string | null): Set<string> {
  const counts = useScreenFields((s) => s.counts);
  return useMemo(() => {
    const prefix = instrument === null ? null : `${instrument}\n`;
    return new Set(prefix === null ? [] : Object.keys(counts).filter((k) => k.startsWith(prefix)).map((k) => k.slice(prefix.length)));
  }, [counts, instrument]);
}
