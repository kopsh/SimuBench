import { describe, expect, it } from "vitest";
import { CLIENT_GROUPS, clientGroup, CLOCK_MODES, clockLabel, oneOf, panelActionLabel } from "./enums";

const t = ((k: string) => `T:${k}`) as never;

describe("runtime guards", () => {
  it("oneOf accepts members and refuses anything else", () => {
    expect(oneOf(CLOCK_MODES, "wall")).toBe("wall");
    expect(oneOf(CLOCK_MODES, "warp")).toBeUndefined();
    expect(oneOf(CLOCK_MODES, 3)).toBeUndefined();
  });
  it("labels translate members and show unknown values as they are", () => {
    expect(clockLabel("stepped", t)).toBe("T:workbench.clockMode.stepped");
    expect(clockLabel("warp", t)).toBe("warp");
    expect(panelActionLabel("single", t)).toBe("T:scope.single");
    expect(panelActionLabel("zap", t)).toBe("zap");
  });
  it("groups communication clients by who sent them", () => {
    expect(CLIENT_GROUPS).toEqual(["script", "agent", "ui", "replay"]);
    expect(clientGroup("pyvisa")).toBe("script");
    expect(clientGroup("sdk")).toBe("script");
    expect(clientGroup("socket:127.0.0.1:5025")).toBe("script");
    expect(clientGroup("hislip:inst0")).toBe("script");
    expect(clientGroup("vxi11:gpib0,1")).toBe("script");
    expect(clientGroup("bench:scope")).toBe("script");
    expect(clientGroup("mcp")).toBe("agent");
    expect(clientGroup("ui")).toBe("ui");
    expect(clientGroup("replay")).toBe("replay");
    expect(clientGroup("new-client")).toBeNull();
    expect(clientGroup(null)).toBeNull();
  });
});
