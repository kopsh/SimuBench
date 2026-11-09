import { create } from "zustand";

export type Selection =
  | { kind: "instrument"; id: string }
  | { kind: "part"; id: string }
  | { kind: "wire"; id: number }
  | { kind: "net"; id: string }
  | null;

/** A request to show one setting in the inspector (#800): not a selection, so it leaves the links between canvas, tree and log alone. `n` makes a repeated request for the same setting count again. */
export type Reveal = { instrument: string; path: string; n: number } | null;

export type Tab =
  | { kind: "canvas" }
  | { kind: "screen"; instrument: string }
  | { kind: "internals"; instrument: string }
  | { kind: "acceptance"; instrument: string };
export type Mode = "run" | "edit";

interface WorkbenchState {
  /** Run drives the session; Edit changes the bench file. Kept across restarts of the session. */
  mode: Mode;
  setMode: (mode: Mode) => void;
  selection: Selection;
  reveal: Reveal;
  /** Select the instrument and ask its inspector to show this setting: the channel picked, the group open, the field scrolled to and focused. */
  revealSetting: (instrument: string, path: string) => void;
  tabs: Tab[];
  active: string; // tab key
  paletteOpen: boolean;
  /** The version history dialog (D35) is open. */
  historyOpen: boolean;
  bottomTab: "comms" | "events" | "problems";
  select: (s: Selection) => void;
  openScreen: (instrument: string) => void;
  /** The read-only view of an instrument's components (issue 20). */
  openInternals: (instrument: string) => void;
  /** The definition's acceptance page (T4): overview, commands, internals, try run, cases, history. */
  openAcceptance: (instrument: string) => void;
  closeTab: (key: string) => void;
  setActive: (key: string) => void;
  setPalette: (open: boolean) => void;
  setHistory: (open: boolean) => void;
  setBottomTab: (tab: WorkbenchState["bottomTab"]) => void;
  reset: () => void;
  /** Reopen screens remembered for a bench (instruments it no longer has are dropped). */
  restore: (tabs: Tab[], active: string) => void;
}

export const tabKey = (t: Tab) => (t.kind === "canvas" ? "canvas" : `${t.kind}:${t.instrument}`);

const initial = {
  selection: null,
  reveal: null as Reveal,
  tabs: [{ kind: "canvas" }] as Tab[],
  active: "canvas",
  paletteOpen: false,
  historyOpen: false,
  bottomTab: "comms" as const,
};

function openTab(tabs: Tab[], tab: Tab & { instrument: string }) {
  const key = tabKey(tab);
  return {
    tabs: tabs.some((t) => tabKey(t) === key) ? tabs : [...tabs, tab],
    active: key,
    selection: { kind: "instrument" as const, id: tab.instrument },
  };
}

export const useWorkbench = create<WorkbenchState>((set) => ({
  ...initial,
  mode: "run",
  setMode: (mode) => set((s) => (s.mode === mode ? s : { mode, active: "canvas" })),
  revealSetting: (instrument, path) =>
    set((s) => ({ selection: { kind: "instrument", id: instrument }, reveal: { instrument, path, n: (s.reveal?.n ?? 0) + 1 } })),
  select: (selection) => set((s) => (JSON.stringify(s.selection) === JSON.stringify(selection) ? s : { selection })),
  openScreen: (instrument) => set((s) => openTab(s.tabs, { kind: "screen", instrument })),
  openInternals: (instrument) => set((s) => openTab(s.tabs, { kind: "internals", instrument })),
  openAcceptance: (instrument) => set((s) => openTab(s.tabs, { kind: "acceptance", instrument })),
  closeTab: (key) =>
    set((s) => {
      if (key === "canvas") return s;
      const tabs = s.tabs.filter((t) => tabKey(t) !== key);
      return { tabs, active: s.active === key ? "canvas" : s.active };
    }),
  setActive: (active) => set({ active }),
  setPalette: (paletteOpen) => set({ paletteOpen }),
  setHistory: (historyOpen) => set({ historyOpen }),
  setBottomTab: (bottomTab) => set({ bottomTab }),
  reset: () => set(initial),
  restore: (tabs, active) =>
    set(() => {
      const all: Tab[] = [{ kind: "canvas" }, ...tabs.filter((t) => t.kind === "screen")];
      return { tabs: all, active: all.some((t) => tabKey(t) === active) ? active : "canvas" };
    }),
}));
