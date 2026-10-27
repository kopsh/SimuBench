import { create } from "zustand";

export type Selection =
  | { kind: "instrument"; id: string }
  | { kind: "dut"; id: string }
  | { kind: "wire"; id: number }
  | { kind: "net"; id: string }
  | null;

export type Tab = { kind: "canvas" } | { kind: "screen"; instrument: string };
export type Mode = "run" | "edit";

interface WorkbenchState {
  /** Run drives the session; Edit changes the bench file. Kept across restarts of the session. */
  mode: Mode;
  setMode: (mode: Mode) => void;
  selection: Selection;
  tabs: Tab[];
  active: string; // tab key
  paletteOpen: boolean;
  bottomTab: "comms" | "events" | "problems";
  select: (s: Selection) => void;
  openScreen: (instrument: string) => void;
  closeTab: (key: string) => void;
  setActive: (key: string) => void;
  setPalette: (open: boolean) => void;
  setBottomTab: (tab: WorkbenchState["bottomTab"]) => void;
  reset: () => void;
}

export const tabKey = (t: Tab) => (t.kind === "canvas" ? "canvas" : `screen:${t.instrument}`);

const initial = { selection: null, tabs: [{ kind: "canvas" }] as Tab[], active: "canvas", paletteOpen: false, bottomTab: "comms" as const };

export const useWorkbench = create<WorkbenchState>((set) => ({
  ...initial,
  mode: "run",
  setMode: (mode) => set((s) => (s.mode === mode ? s : { mode, active: "canvas" })),
  select: (selection) => set((s) => (JSON.stringify(s.selection) === JSON.stringify(selection) ? s : { selection })),
  openScreen: (instrument) =>
    set((s) => {
      const key = `screen:${instrument}`;
      const tabs = s.tabs.some((t) => tabKey(t) === key) ? s.tabs : [...s.tabs, { kind: "screen" as const, instrument }];
      return { tabs, active: key, selection: { kind: "instrument", id: instrument } };
    }),
  closeTab: (key) =>
    set((s) => {
      if (key === "canvas") return s;
      const tabs = s.tabs.filter((t) => tabKey(t) !== key);
      return { tabs, active: s.active === key ? "canvas" : s.active };
    }),
  setActive: (active) => set({ active }),
  setPalette: (paletteOpen) => set({ paletteOpen }),
  setBottomTab: (bottomTab) => set({ bottomTab }),
  reset: () => set(initial),
}));
