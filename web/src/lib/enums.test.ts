import { describe, expect, it } from "vitest";
import { CLOCK_MODES, clockLabel, oneOf, panelActionLabel } from "./enums";

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
});
