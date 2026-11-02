import { describe, expect, it } from "vitest";
import { accessoryLabel, optionLabel } from "./model";

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

describe("option labels (#281)", () => {
  it("names common SCPI words in the panel's language and keeps the mnemonic otherwise", () => {
    expect(optionLabel({ option_labels: null }, "SINusoid")).toBe("正弦");
    expect(optionLabel({ option_labels: null }, "SQUARE")).toBe("方波");
    expect(optionLabel({ option_labels: null }, "IMMediate")).toBe("立即");
    expect(optionLabel({ option_labels: { SINusoid: "Sine wave" } }, "SINusoid")).toBe("Sine wave");
    expect(optionLabel({ option_labels: null }, "ONEMeg")).toBe("ONEM");
  });
});
