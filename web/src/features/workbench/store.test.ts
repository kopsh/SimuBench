import { beforeEach, describe, expect, it } from "vitest";
import { INSPECTOR_SHOWN, INSPECTOR_SIZE, tabKindOf, useWorkbench } from "./store";

describe("revealSetting (#800)", () => {
  beforeEach(() => useWorkbench.getState().reset());

  it("selects the instrument and asks its inspector for the setting, without changing the tabs", () => {
    const { openInternals, revealSetting } = useWorkbench.getState();
    openInternals("scope");
    const tabs = useWorkbench.getState().tabs;
    revealSetting("scope", "ch2.scale");
    const s = useWorkbench.getState();
    expect(s.selection).toEqual({ kind: "instrument", id: "scope" });
    expect(s.reveal).toMatchObject({ instrument: "scope", path: "ch2.scale" });
    expect(s.tabs).toBe(tabs);
    expect(s.active).toBe("internals:scope");
  });

  it("counts a repeated request for the same setting again", () => {
    const { revealSetting } = useWorkbench.getState();
    revealSetting("scope", "timebase.scale");
    const first = useWorkbench.getState().reveal!.n;
    revealSetting("scope", "timebase.scale");
    expect(useWorkbench.getState().reveal!.n).toBeGreaterThan(first);
  });

  it("is cleared with the rest when the bench is left", () => {
    useWorkbench.getState().revealSetting("scope", "timebase.scale");
    useWorkbench.getState().reset();
    expect(useWorkbench.getState().reveal).toBeNull();
  });
});

describe("consumeReveal (#819)", () => {
  beforeEach(() => useWorkbench.getState().reset());

  it("spends the request, so a later mount of the inspector does not act on it again", () => {
    useWorkbench.getState().revealSetting("scope", "timebase.scale");
    const n = useWorkbench.getState().reveal!.n;
    useWorkbench.getState().consumeReveal(n);
    expect(useWorkbench.getState().reveal).toBeNull();
  });

  it("leaves a newer request alone", () => {
    const { revealSetting, consumeReveal } = useWorkbench.getState();
    revealSetting("scope", "timebase.scale");
    const old = useWorkbench.getState().reveal!.n;
    revealSetting("scope", "ch2.scale");
    consumeReveal(old);
    expect(useWorkbench.getState().reveal).toMatchObject({ path: "ch2.scale" });
  });

  it("still serves a repeated request after the first was spent", () => {
    const { revealSetting, consumeReveal } = useWorkbench.getState();
    revealSetting("scope", "timebase.scale");
    consumeReveal(useWorkbench.getState().reveal!.n);
    revealSetting("scope", "timebase.scale");
    expect(useWorkbench.getState().reveal).toMatchObject({ path: "timebase.scale" });
  });
});

describe("the inspector beside each kind of tab (#1221)", () => {
  beforeEach(() => {
    localStorage.clear();
    useWorkbench.setState({ inspector: { ...INSPECTOR_SHOWN } });
  });

  it("starts shown beside the canvas and screens, folded beside the internals and acceptance pages", () => {
    expect(INSPECTOR_SHOWN).toEqual({ canvas: true, screen: true, internals: false, acceptance: false });
  });

  it("names the kind of a tab from its key", () => {
    expect(tabKindOf("canvas")).toBe("canvas");
    expect(tabKindOf("screen:scope")).toBe("screen");
    expect(tabKindOf("internals:awg")).toBe("internals");
    expect(tabKindOf("acceptance:sa")).toBe("acceptance");
  });

  it("keeps the user's choice for one kind of tab, in this browser, without changing the others", () => {
    useWorkbench.getState().setInspector("screen", false);
    expect(useWorkbench.getState().inspector).toEqual({ canvas: true, screen: false, internals: false, acceptance: false });
    expect(JSON.parse(localStorage.getItem("simubench:inspector-shown") ?? "null")).toMatchObject({ screen: false });
  });

  it("is shown beside the current tab for a selection or a locate request, until another tab is chosen", () => {
    const st = () => useWorkbench.getState();
    st().reset();
    st().openInternals("scope"); // opening a tab selects its instrument: not a request to show
    expect(st().lentTo).toBeNull();
    st().select({ kind: "part", id: "dut" });
    expect(st().lentTo).toBe("internals:scope");
    st().setActive("canvas");
    expect(st().lentTo).toBeNull();
    st().setActive("internals:scope");
    st().revealSetting("scope", "timebase.scale");
    expect(st().lentTo).toBe("internals:scope");
    st().setInspector("internals", false); // the user's own choice ends it
    expect(st().lentTo).toBeNull();
  });

  it("is not reset when the bench is left", () => {
    useWorkbench.getState().setInspector("canvas", false);
    useWorkbench.getState().reset();
    expect(useWorkbench.getState().inspector.canvas).toBe(false);
  });
});

describe("the inspector's width beside each kind of tab (#1229)", () => {
  beforeEach(() => {
    localStorage.clear();
    useWorkbench.setState({ inspectorSize: { ...INSPECTOR_SIZE } });
  });

  it("is narrower beside a screen, whose settings it leaves out", () => {
    expect(INSPECTOR_SIZE.screen).toBeLessThan(INSPECTOR_SIZE.canvas);
  });

  it("keeps a dragged width for that kind of tab only, in this browser", () => {
    useWorkbench.getState().setInspectorSize("screen", 21.04);
    expect(useWorkbench.getState().inspectorSize).toEqual({ ...INSPECTOR_SIZE, screen: 21 });
    expect(JSON.parse(localStorage.getItem("simubench:inspector-size") ?? "null")).toMatchObject({ screen: 21 });
  });
});

describe("showDefinition (#1250)", () => {
  beforeEach(() => useWorkbench.getState().reset());

  it("turns the instrument's acceptance tab into the copy's page at the section it was on", () => {
    const { openAcceptance, showDefinition } = useWorkbench.getState();
    openAcceptance("scope");
    showDefinition("scope", "my-keysight-uxr1104b", "build");
    const s = useWorkbench.getState();
    expect(s.tabs.filter((t) => t.kind === "acceptance")).toEqual([
      { kind: "acceptance", instrument: "scope", definition: "my-keysight-uxr1104b", section: "build" },
    ]);
    expect(s.active).toBe("acceptance:scope");
  });
});

describe("the tab and the inspector show the same instrument", () => {
  const st = () => useWorkbench.getState();
  beforeEach(() => st().reset());

  it("selects a tab's instrument when the tab is chosen", () => {
    st().openScreen("scope");
    st().openScreen("dmm");
    expect(st().selection).toEqual({ kind: "instrument", id: "dmm" });
    st().setActive("screen:scope");
    expect(st().selection).toEqual({ kind: "instrument", id: "scope" });
    st().setActive("screen:dmm");
    expect(st().selection).toEqual({ kind: "instrument", id: "dmm" });
  });

  it("keeps the selection when the canvas is chosen", () => {
    st().openScreen("scope");
    st().setActive("canvas");
    expect(st().selection).toEqual({ kind: "instrument", id: "scope" });
  });

  it("moves to another instrument's tab of the same kind when that instrument is selected", () => {
    st().openScreen("scope");
    st().select({ kind: "instrument", id: "awg" });
    expect(st().active).toBe("screen:awg");
    expect(st().tabs.map((t) => t.kind + ":" + ("instrument" in t ? t.instrument : ""))).toContain("screen:awg");
    expect(st().selection).toEqual({ kind: "instrument", id: "awg" });
    expect(st().lentTo).toBe("screen:awg");
  });

  it("selects on the canvas without changing the tab", () => {
    st().select({ kind: "instrument", id: "awg" });
    expect(st().active).toBe("canvas");
    expect(st().tabs).toHaveLength(1);
  });

  it("follows a locate request for another instrument's setting to that instrument's tab", () => {
    st().openInternals("scope");
    st().revealSetting("awg", "ch1.freq");
    expect(st().active).toBe("internals:awg");
    expect(st().selection).toEqual({ kind: "instrument", id: "awg" });
    expect(st().reveal).toMatchObject({ instrument: "awg", path: "ch1.freq" });
    expect(st().lentTo).toBe("internals:awg");
  });

  it("selects the active tab's instrument when the remembered tabs are restored", () => {
    st().restore([{ kind: "screen", instrument: "scope" }], "screen:scope");
    expect(st().selection).toEqual({ kind: "instrument", id: "scope" });
  });
});
