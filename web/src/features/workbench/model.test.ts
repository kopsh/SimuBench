import { describe, expect, it } from "vitest";
import { accessoryLabel } from "./model";

describe("accessoryLabel", () => {
  it("names a lead by its resistance (issue 91: the lead shows on the canvas)", () => {
    expect(accessoryLabel("lead", { r: 0.5 })).toBe("0.5 Ω");
    expect(accessoryLabel("lead", {})).toBe("0.1 Ω");
  });

  it("keeps the other accessories' labels", () => {
    expect(accessoryLabel("passive_probe", { attenuation: 10 })).toBe("10×");
    expect(accessoryLabel("coax_cable", {})).toBe("coax");
    expect(accessoryLabel("unknown_type", {})).toBe("unknown_type");
  });
});
