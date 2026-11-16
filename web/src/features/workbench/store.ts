import { create } from "zustand";
import { load, save } from "@/lib/storage";

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
  /**
   * `definition`: the page shows this definition instead of the instrument's own — the copy just
   * made with "copy as my instrument", before the bench uses it (#1250); `section`: where it opens.
   */
  | { kind: "acceptance"; instrument: string; definition?: string; section?: string };
export type Mode = "run" | "edit";
export type TabKind = Tab["kind"];

/**
 * Whether the inspector is shown beside each kind of tab (#1221). Beside the canvas and an
 * instrument screen it is shown: it holds the SCPI console and the settings a screen does not have.
 * The internals and acceptance pages carry their own side panels, so there it starts folded away
 * and gives its width to the page. The user's choice for a kind (the button at the end of the tab
 * bar) is kept in this browser.
 */
export const INSPECTOR_SHOWN: Record<TabKind, boolean> = { canvas: true, screen: true, internals: false, acceptance: false };

/**
 * The inspector's width beside each kind of tab, in per cent of the row (#1229). Beside a screen it
 * is narrower: the settings the screen shows are left out of it, and the width goes to the screen.
 * Dragging its edge sets the width for that kind of tab, kept in this browser.
 */
export const INSPECTOR_SIZE: Record<TabKind, number> = { canvas: 24, screen: 18, internals: 24, acceptance: 24 };

/** The tab kind a tab key names ("screen:scope" → "screen"). */
export const tabKindOf = (key: string): TabKind => (key === "canvas" ? "canvas" : (key.split(":")[0] as TabKind));

interface WorkbenchState {
  /** Run drives the session; Edit changes the bench file. Kept across restarts of the session. */
  mode: Mode;
  setMode: (mode: Mode) => void;
  selection: Selection;
  /**
   * The tab beside which a folded inspector is shown for the moment (#1221): the user selected
   * something there (a source on a screen, an instrument in the tree) or asked to see a setting
   * (#800). Opening a tab, which selects its instrument, does not count; another tab ends it.
   */
  lentTo: string | null;
  reveal: Reveal;
  /** Select the instrument and ask its inspector to show this setting: the channel picked, the group open, the field scrolled to and focused. */
  revealSetting: (instrument: string, path: string) => void;
  /** The inspector has dealt with request `n` (shown the field or found nothing): a later mount must not act on it again (#819). */
  consumeReveal: (n: number) => void;
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
  /** Show `definition` in the instrument's acceptance tab, at `section` (#1250): the tab stays, its page changes. */
  showDefinition: (instrument: string, definition: string, section: string) => void;
  closeTab: (key: string) => void;
  setActive: (key: string) => void;
  setPalette: (open: boolean) => void;
  setHistory: (open: boolean) => void;
  setBottomTab: (tab: WorkbenchState["bottomTab"]) => void;
  /** The AI assistant panel (#877) is shown; remembered in this browser, kept across sessions. */
  assistantOpen: boolean;
  setAssistant: (open: boolean) => void;
  /** The inspector shown beside each kind of tab (INSPECTOR_SHOWN; remembered in this browser). */
  inspector: Record<TabKind, boolean>;
  setInspector: (kind: TabKind, shown: boolean) => void;
  /** The inspector's width beside each kind of tab (INSPECTOR_SIZE; remembered in this browser). */
  inspectorSize: Record<TabKind, number>;
  setInspectorSize: (kind: TabKind, percent: number) => void;
  reset: () => void;
  /** Reopen screens remembered for a bench (instruments it no longer has are dropped). */
  restore: (tabs: Tab[], active: string) => void;
}

export const tabKey = (t: Tab) => (t.kind === "canvas" ? "canvas" : `${t.kind}:${t.instrument}`);

const initial = {
  selection: null,
  lentTo: null as string | null,
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
    lentTo: null,
  };
}

export const useWorkbench = create<WorkbenchState>((set) => ({
  ...initial,
  mode: "run",
  assistantOpen: load<boolean>("assistant-open", false),
  setAssistant: (assistantOpen) => {
    save("assistant-open", assistantOpen);
    set({ assistantOpen });
  },
  inspector: { ...INSPECTOR_SHOWN, ...load<Partial<Record<TabKind, boolean>>>("inspector-shown", {}) },
  setInspector: (kind, shown) =>
    set((s) => {
      // Folding a lent inspector is the same choice as the one stored: it ends the lending.
      if (s.inspector[kind] === shown) return s.lentTo === null ? s : { lentTo: null };
      const inspector = { ...s.inspector, [kind]: shown };
      save("inspector-shown", inspector);
      return { inspector, lentTo: null };
    }),
  inspectorSize: { ...INSPECTOR_SIZE, ...load<Partial<Record<TabKind, number>>>("inspector-size", {}) },
  setInspectorSize: (kind, percent) =>
    set((s) => {
      const size = Math.round(percent * 10) / 10;
      if (s.inspectorSize[kind] === size) return s;
      const inspectorSize = { ...s.inspectorSize, [kind]: size };
      save("inspector-size", inspectorSize);
      return { inspectorSize };
    }),
  setMode: (mode) => set((s) => (s.mode === mode ? s : { mode, active: "canvas", lentTo: null })),
  revealSetting: (instrument, path) =>
    set((s) => ({
      selection: { kind: "instrument", id: instrument },
      reveal: { instrument, path, n: (s.reveal?.n ?? 0) + 1 },
      lentTo: s.active,
    })),
  consumeReveal: (n) => set((s) => (s.reveal?.n === n ? { reveal: null } : s)),
  select: (selection) =>
    set((s) => (JSON.stringify(s.selection) === JSON.stringify(selection) ? s : { selection, lentTo: s.active })),
  openScreen: (instrument) => set((s) => openTab(s.tabs, { kind: "screen", instrument })),
  openInternals: (instrument) => set((s) => openTab(s.tabs, { kind: "internals", instrument })),
  openAcceptance: (instrument) => set((s) => openTab(s.tabs, { kind: "acceptance", instrument })),
  showDefinition: (instrument, definition, section) =>
    set((s) => {
      const tab: Tab = { kind: "acceptance", instrument, definition, section };
      const key = tabKey(tab);
      const tabs = s.tabs.some((t) => tabKey(t) === key) ? s.tabs.map((t) => (tabKey(t) === key ? tab : t)) : [...s.tabs, tab];
      return { tabs, active: key };
    }),
  closeTab: (key) =>
    set((s) => {
      if (key === "canvas") return s;
      const tabs = s.tabs.filter((t) => tabKey(t) !== key);
      return s.active === key ? { tabs, active: "canvas", lentTo: null } : { tabs };
    }),
  setActive: (active) => set((s) => (s.active === active ? s : { active, lentTo: null })),
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
