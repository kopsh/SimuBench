import { describe, expect, it } from "vitest";
import { sweepStatus } from "./SpectrumScreen";

describe("sweepStatus", () => {
  it("is running on the wall and scaled clocks", () => {
    expect(sweepStatus("wall", true)).toBe("running");
    expect(sweepStatus("scaled", true)).toBe("running");
  });
  it("explains a standing trace on the command and stepped clocks", () => {
    expect(sweepStatus("command", true)).toBe("clock");
    expect(sweepStatus("stepped", true)).toBe("clock");
  });
  it("shows a single sweep as held whatever the clock", () => {
    expect(sweepStatus("wall", false)).toBe("single");
    expect(sweepStatus("command", false)).toBe("single");
  });
});
