import { beforeEach, describe, expect, it } from "vitest";
import { useWorkbench } from "./store";

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
