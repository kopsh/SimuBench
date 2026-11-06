import { describe, expect, it } from "vitest";
import type { StateVarInfo } from "@/api/client";
import { accessoryLabel, hasLabel, optionLabel, slotRows, splitSettings } from "./model";

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

describe("inspector settings (#728)", () => {
  const v = (path: string, channel: number | null = null, extra: Partial<StateVarInfo> = {}) =>
    ({ path, channel, type: "enum", value: "", ...extra }) as unknown as StateVarInfo;

  it("puts the summary first, flat settings after it and folds the rest by first segment", () => {
    const vars = [
      v("timebase.scale", null, { label: "时基" }),
      v("trigger.source"),
      v("trigger.level"),
      v("interleaved"),
      v("comm.header"),
      v("waveform.sp"),
      v("measure.p1.kind"),
    ];
    const { main, groups } = splitSettings(vars, ["trigger.source", "timebase.scale", "absent.key"]);
    expect(main.map((x) => x.path)).toEqual(["trigger.source", "timebase.scale", "interleaved"]);
    expect(groups.map((g) => g.id)).toEqual(["trigger", "measure", "comm", "waveform"]);
    expect(groups.map((g) => g.remote)).toEqual([false, false, true, true]);
    expect(groups[0].vars.map((x) => x.path)).toEqual(["trigger.level"]);
  });

  it("reads a channel's summary names without the channel prefix", () => {
    const vars = [v("ch2.scale", 2), v("ch2.coupling", 2), v("ch2.bw.limit", 2)];
    const { main, groups } = splitSettings(vars, ["scale"]);
    expect(main.map((x) => x.path)).toEqual(["ch2.scale", "ch2.coupling"]);
    expect(groups.map((g) => g.id)).toEqual(["bw"]);
  });

  it("keeps every setting of an instrument that declares no summary visible", () => {
    const vars = [v("frequency", 1), v("amplitude", 1), v("output", 1)];
    const { main, groups } = splitSettings(vars, []);
    expect(main).toHaveLength(3);
    expect(groups).toEqual([]);
  });

  it("joins a measurement slot's kind and source into one row", () => {
    const vars = [v("measure.p1.kind"), v("measure.p1.source"), v("measure.p2.kind"), v("measure.mode")];
    const { slots, rest } = slotRows(vars);
    expect(slots.map((s) => [s.label, s.vars.length])).toEqual([["P1", 2], ["P2", 1]]);
    expect(rest.map((x) => x.path)).toEqual(["measure.mode"]);
  });

  it("shows an empty slot as a word, not as the raw sentinel", () => {
    expect(optionLabel({ option_labels: null }, "NULL")).toBe("无");
    expect(hasLabel({ label: null, label_en: null })).toBe(false);
    expect(hasLabel({ label: "时基", label_en: null })).toBe(true);
  });
});
