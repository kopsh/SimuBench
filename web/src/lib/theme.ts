import { create } from "zustand";
import { load, save } from "./storage";

export type ThemePreference = "system" | "light" | "dark";
type Resolved = "light" | "dark";

const media = () => (typeof window !== "undefined" ? window.matchMedia("(prefers-color-scheme: dark)") : null);

function resolve(pref: ThemePreference): Resolved {
  if (pref === "system") return media()?.matches ? "dark" : "light";
  return pref;
}

function apply(resolved: Resolved) {
  document.documentElement.classList.toggle("dark", resolved === "dark");
  document.documentElement.style.colorScheme = resolved;
}

interface ThemeState {
  preference: ThemePreference;
  resolved: Resolved;
  setPreference: (p: ThemePreference) => void;
}

export const useTheme = create<ThemeState>((set) => {
  const preference = load<ThemePreference>("theme", "system");
  const resolved = resolve(preference);
  if (typeof document !== "undefined") apply(resolved);
  media()?.addEventListener("change", () => {
    const pref = useTheme.getState().preference;
    if (pref === "system") {
      const r = resolve(pref);
      apply(r);
      set({ resolved: r });
    }
  });
  return {
    preference,
    resolved,
    setPreference: (p) => {
      save("theme", p);
      const r = resolve(p);
      apply(r);
      set({ preference: p, resolved: r });
    },
  };
});
