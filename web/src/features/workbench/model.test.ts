import { describe, expect, it } from "vitest";
import type { StateVarInfo } from "@/api/client";
import type { InstrumentInfo } from "@/api/client";
import benches from "./canvas/benches.fixture.json";
import canvas from "./canvas/canvas.fixture.json";
import panels from "./screens/panel.fixture.json";
import { accessoryLabel, channelVars, globalVars, groupOpen, hasLabel, OTHER, optionLabel, slotRows, splitSettings, stepNumber } from "./model";

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

  it("puts the summary first and folds the rest by first segment, flat unnamed ones in other", () => {
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
    expect(main.map((x) => x.path)).toEqual(["trigger.source", "timebase.scale"]);
    expect(groups.map((g) => g.id)).toEqual(["trigger", "measure", OTHER, "comm", "waveform"]);
    expect(groups.map((g) => g.remote)).toEqual([false, false, false, true, true]);
    expect(groups[0].vars.map((x) => x.path)).toEqual(["trigger.level"]);
  });

  it("reads a channel's summary names without the channel prefix", () => {
    const vars = [v("ch2.scale", 2), v("ch2.coupling", 2, { label: "耦合" }), v("ch2.bw.limit", 2)];
    const { main, groups } = splitSettings(vars, ["scale"]);
    expect(main.map((x) => x.path)).toEqual(["ch2.scale", "ch2.coupling"]);
    expect(groups.map((g) => g.id)).toEqual(["bw"]);
  });

  it("keeps a named flat setting in the main tier and parks an unnamed one in other", () => {
    const vars = [v("frequency", 1, { label: "频率" }), v("skew", 1), v("amplitude", 1, { label_en: "Amplitude" })];
    const { main, groups } = splitSettings(vars, []);
    expect(main.map((x) => x.path)).toEqual(["frequency", "amplitude"]);
    expect(groups.map((g) => g.id)).toEqual([OTHER]);
    expect(groups[0].vars.map((x) => x.path)).toEqual(["skew"]);
  });

  it("orders the groups: named ones, other, then the remote-programming ones", () => {
    const vars = [v("comm.header"), v("skew"), v("trigger.hold"), v("waveform.sp")];
    expect(splitSettings(vars, []).groups.map((g) => g.id)).toEqual(["trigger", OTHER, "comm", "waveform"]);
  });

  it("opens the groups of named settings and folds remote and unnamed ones", () => {
    const vars = [v("sense.range", null, { label: "量程" }), v("sense.nplc", null, { label_en: "NPLC" }), v("trigger.hold"), v("comm.header", null, { label: "头" })];
    const open = Object.fromEntries(splitSettings(vars, []).groups.map((g) => [g.id, groupOpen(g)]));
    expect(open).toEqual({ sense: true, trigger: false, comm: false });
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

describe("inspector settings of real definitions (#728)", () => {
  // Real InstrumentInfo snapshots: counter, source-measure unit, scope, generator, multimeter, calibrator.
  const all = [
    ...Object.values(benches).flatMap((b) => b.session.instruments as unknown[]),
    ...(canvas.session.instruments as unknown[]),
    ...Object.values(panels).map((p) => p.instrument as unknown),
  ] as InstrumentInfo[];

  it("covers several instrument classes", () => {
    expect(new Set(all.map((i) => i.instrument_class)).size).toBeGreaterThanOrEqual(4);
  });

  for (const inst of all) {
    const tiers = [
      { name: "instrument", vars: globalVars(inst), summary: inst.display.summary },
      ...Array.from({ length: inst.channels }, (_, i) => ({
        name: `CH${i + 1}`,
        vars: channelVars(inst, i + 1),
        summary: inst.display.channel_summary,
      })),
    ];
    for (const tier of tiers) {
      it(`${inst.model} ${tier.name}: every setting once, nothing unnamed in the main tier, something to see`, () => {
        const { main, groups } = splitSettings(tier.vars, tier.summary);
        const placed = [...main, ...groups.flatMap((g) => g.vars)].map((x) => x.path);
        expect([...placed].sort()).toEqual(tier.vars.map((x) => x.path).sort());
        const summary = new Set(tier.summary);
        for (const x of main) expect(hasLabel(x) || summary.has(x.path.replace(/^ch\d+\./, ""))).toBe(true);
        if (tier.vars.length) expect(main.length > 0 || groups.length > 0).toBe(true);
      });
    }
  }
});

describe("stepping a number (#738)", () => {
  const spec = (type: "float" | "int", extra: Record<string, unknown> = {}) => ({ type, min: null, max: null, ladder: null, ...extra }) as never;

  it("moves along a 1-2-5 ladder, up and down, across a decade", () => {
    const l = spec("float", { ladder: "1-2-5" });
    expect(stepNumber(l, 0.05, 1)).toBe(0.1);
    expect(stepNumber(l, 0.1, 1)).toBe(0.2);
    expect(stepNumber(l, 0.2, 1)).toBe(0.5);
    expect(stepNumber(l, 0.5, 1)).toBe(1);
    expect(stepNumber(l, 1, -1)).toBe(0.5);
    expect(stepNumber(l, 0.1, -1)).toBe(0.05);
    expect(stepNumber(l, 2e-6, -1)).toBe(1e-6);
    expect(stepNumber(l, 1e-6, -1)).toBe(5e-7);
  });

  it("moves along a 1-3 ladder and through an explicit list, and stops at its ends", () => {
    expect(stepNumber(spec("float", { ladder: "1-3" }), 0.3, 1)).toBe(1);
    expect(stepNumber(spec("float", { ladder: "1-3" }), 1, -1)).toBe(0.3);
    const list = spec("float", { ladder: [10, 20, 50, 100] });
    expect(stepNumber(list, 20, 1)).toBe(50);
    expect(stepNumber(list, 20, -1)).toBe(10);
    expect(stepNumber(list, 100, 1)).toBeNull();
    expect(stepNumber(list, 10, -1)).toBeNull();
    expect(stepNumber(list, 30, 1)).toBe(50); // between two rungs: the next one
  });

  it("moves a ladder's decade at a time when coarse", () => {
    expect(stepNumber(spec("float", { ladder: "1-2-5" }), 0.1, 1, true)).toBe(1);
    expect(stepNumber(spec("float", { ladder: "1-2-5" }), 1, -1, true)).toBe(0.1);
  });

  it("steps an integer by one and a continuous value by a tenth of its decade", () => {
    expect(stepNumber(spec("int"), 8, 1)).toBe(9);
    expect(stepNumber(spec("int"), 8, -1, true)).toBe(-2);
    expect(stepNumber(spec("float"), 1500, 1)).toBe(1600);
    expect(stepNumber(spec("float"), 1500, -1)).toBe(1400);
    expect(stepNumber(spec("float"), 0.5, 1)).toBe(0.51);
    expect(stepNumber(spec("float"), 1500, 1, true)).toBe(2500);
  });

  it("steps down from a power of ten into the decade below", () => {
    expect(stepNumber(spec("float"), 1000, -1)).toBe(990);
    expect(stepNumber(spec("float"), 1000, 1)).toBe(1100);
  });

  it("handles zero and negative values", () => {
    expect(stepNumber(spec("float", { min: -1, max: 1 }), 0, 1)).toBe(0.02);
    expect(stepNumber(spec("float"), 0, 1)).toBe(1);
    expect(stepNumber(spec("float"), -1500, 1)).toBe(-1400);
    expect(stepNumber(spec("float"), -1500, -1)).toBe(-1600);
  });

  it("stays inside the setting's range and says so at the limit", () => {
    const r = spec("float", { min: 0, max: 10 });
    expect(stepNumber(r, 9.99, 1)).toBe(10);
    expect(stepNumber(r, 10, 1)).toBeNull();
    expect(stepNumber(r, 0, -1)).toBeNull();
    expect(stepNumber(spec("int", { min: 1, max: 4 }), 4, 1)).toBeNull();
  });

  it("does not step what is not a finite number", () => {
    expect(stepNumber(spec("float"), Infinity, -1)).toBeNull();
    expect(stepNumber(spec("float"), Number.NaN, 1)).toBeNull();
  });

  it("does not leave floating-point dust behind", () => {
    expect(stepNumber(spec("float"), 0.3, 1)).toBe(0.31);
    expect(stepNumber(spec("float"), 0.7, 1)).toBe(0.71);
  });
});
