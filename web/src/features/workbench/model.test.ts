import { describe, expect, it } from "vitest";
import type { StateVarInfo } from "@/api/client";
import type { InstrumentInfo } from "@/api/client";
import cases from "@/i18n/option-words.cases.json";
import benches from "./canvas/benches.fixture.json";
import canvas from "./canvas/canvas.fixture.json";
import panels from "./screens/panel.fixture.json";
import { accessoryLabel, autoBottomTab, channelVars, distinctOptions, FIELD_PAGE, fieldsShown, findSettings, formatValue, globalVars, groupName, groupOpen, groupsOpenByDefault, hasLabel, instanceFamilies, isAutoValue, netTitle, OPEN_ROWS_MAX, OTHER, optionLabel, panelSections, panelSettings, slotRows, splitSettings, stepNumber, whenHolds, wordOf } from "./model";

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
  it("names the inspector's former raw mnemonics, by the option as written and by numbered family (#1257)", () => {
    expect(optionLabel({ option_labels: null }, "PERCent")).toBe("百分比");
    expect(optionLabel({ option_labels: null }, "SVERtical")).toBe("垂直堆叠");
    expect(optionLabel({ option_labels: null }, "HANNing")).toBe("Hanning 窗");
    expect(optionLabel({ option_labels: null }, "COMMa")).toBe("逗号");
    expect(optionLabel({ option_labels: null }, "COMMonmode")).toBe("共模");
    expect(optionLabel({ option_labels: null }, "CHANnel3")).toBe("通道 3");
    expect(optionLabel({ option_labels: null }, "DIV10")).toBe("衰减 ÷10");
  });

  // One shared case table, both lookups: tests/test_option_words.py runs the same JSON through
  // word_of, so the two cannot drift apart silently (#1286).
  it.each(cases as [string, string | null][])("looks %s up like the Python word_of does", (option, zh) => {
    expect(wordOf(option)?.[0] ?? null).toBe(zh);
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

  it("folds a group of named settings that is too long to show at once (#1299)", () => {
    const named = (path: string) => v(path, null, { label: path });
    const short = Array.from({ length: OPEN_ROWS_MAX }, (_, i) => named(`acquire.k${i}`));
    const long = Array.from({ length: OPEN_ROWS_MAX + 1 }, (_, i) => named(`trigger.k${i}`));
    const open = Object.fromEntries(splitSettings([...short, ...long], []).groups.map((g) => [g.id, groupOpen(g)]));
    expect(open).toEqual({ acquire: true, trigger: false });
  });

  it("opens short groups only within one page of rows, however many the definition has (#1580)", () => {
    const named = (path: string) => v(path, null, { label: path });
    // Names without a number, so they stay separate groups rather than one numbered family.
    const id = (g: number) => `g${"abcdefghij"[g % 10]}${"abcde"[Math.floor(g / 10)]}`;
    const vars = Array.from({ length: 50 }, (_, g) => Array.from({ length: 10 }, (_, i) => named(`${id(g)}.k${i}`))).flat();
    const { groups } = splitSettings(vars, []);
    expect(groups.every(groupOpen)).toBe(true);
    const open = groupsOpenByDefault(groups, 0);
    expect([...open]).toEqual([id(0), id(1), id(2), id(3)]);
    expect(groupsOpenByDefault(groups, FIELD_PAGE).size).toBe(0);
  });

  it("joins four or more numbered groups into one, numbers in order, folded and named by the prefix (#1299)", () => {
    const fn = (n: number) => [v(`function${n}.operator`, null, { label: "运算" }), v(`function${n}.source1`, null, { label: "源 1" })];
    const vars = [v("timebase.scale", null, { label: "时基" }), ...[10, 2, 1, 3].flatMap(fn), v("marker1.x"), v("marker2.x")];
    const { groups } = splitSettings(vars, []);
    expect(groups.map((g) => g.id)).toEqual(["timebase", "function", "marker1", "marker2"]);
    const joined = groups[1];
    expect(joined.numbered?.instances).toEqual(["1", "2", "3", "10"]);
    expect(joined.numbered?.byInstance.get("10")?.map((x) => x.path)).toEqual(["function10.operator", "function10.source1"]);
    expect(joined.vars).toHaveLength(8);
    expect(groupOpen(joined)).toBe(false);
    expect(groupName("function", true)).toBe("数学函数");
    expect(groupName("function")).toBe("功能");
  });

  it("puts exactly the summary on the panel when the definition gives one, the named flat settings beside it in the rest (#1313)", () => {
    const vars = [v("ch1.display", 1, { label: "显示" }), v("ch1.scale", 1, { label: "档位" }), v("ch1.invert", 1, { label: "反相" }), v("ch1.bw.limit", 1)];
    const { panel, rest } = panelSettings(vars, ["scale", "display"]);
    expect(panel.map((x) => x.path)).toEqual(["ch1.scale", "ch1.display"]);
    expect(rest.map((x) => x.path)).toEqual(["ch1.invert", "ch1.bw.limit"]);
  });

  it("puts the named flat settings on the panel when there is no summary (#1313)", () => {
    const vars = [v("frequency", null, { label: "频率" }), v("skew"), v("mod.depth", null, { label: "深度" })];
    const { panel, rest } = panelSettings(vars, []);
    expect(panel.map((x) => x.path)).toEqual(["frequency"]);
    expect(rest.map((x) => x.path)).toEqual(["skew", "mod.depth"]);
  });

  it("finds settings by every word of the query in either name or the path, ignoring case (#1313)", () => {
    const vars = [
      v("trigger.level1", null, { label: "触发电平 CH1", label_en: "Trigger level CH1" }),
      v("trigger.level2", null, { label: "触发电平 CH2", label_en: "Trigger level CH2" }),
      v("acquire.srate", null, { label: "采样率设定" }),
    ];
    expect(findSettings(vars, "触发电平 ch2").hits.map((x) => x.path)).toEqual(["trigger.level2"]);
    expect(findSettings(vars, "LEVEL").total).toBe(2);
    expect(findSettings(vars, "acquire.s").hits.map((x) => x.path)).toEqual(["acquire.srate"]);
    expect(findSettings(vars, "  ").total).toBe(0);
  });

  it("lists at most the limit of matches and says how many matched (#1313)", () => {
    const vars = Array.from({ length: 5 }, (_, i) => v(`display.bookmark${i}.color`));
    const found = findSettings(vars, "bookmark", 3);
    expect(found.hits).toHaveLength(3);
    expect(found.total).toBe(5);
  });

  it("holds a when while every setting has its value, a list meaning any, a channel name read in that channel (#1315)", () => {
    const vars = new Map(
      [v("trigger.mode", null, { value: "GLITch" }), v("acquire.average", null, { value: true }), v("ch2.bwlimit", 2, { value: false })].map((x) => [x.path, x]),
    );
    expect(whenHolds({ "trigger.mode": "GLITch", "acquire.average": true }, vars)).toBe(true);
    expect(whenHolds({ "trigger.mode": ["EDGE", "GLITch"] }, vars)).toBe(true);
    expect(whenHolds({ "trigger.mode": "EDGE" }, vars)).toBe(false);
    expect(whenHolds({ bwlimit: false }, vars, 2)).toBe(true);
    expect(whenHolds({ "absent.setting": 1 }, vars)).toBe(false);
  });

  it("lists a declared panel's groups as they stand: when-hidden controls and groups out, empty groups dropped (#1315)", () => {
    const vars = new Map(
      [
        v("trigger.mode", null, { value: "EDGE" }),
        v("trigger.slope"),
        v("trigger.glitch_width"),
        v("ch3.scale", 3),
        v("ch3.offset", 3),
      ].map((x) => [x.path, x]),
    );
    const groups = [
      {
        label: "触发",
        controls: [
          { state: "trigger.mode", when: {} },
          { state: "trigger.slope", when: { "trigger.mode": "EDGE" } },
          { state: "trigger.glitch_width", when: { "trigger.mode": "GLITch" } },
        ],
        when: {},
      },
      { label: "毛刺", controls: [{ state: "trigger.glitch_width", when: {} }], when: { "trigger.mode": "GLITch" } },
    ];
    expect(panelSections(groups, vars, () => true).map((g) => [g.label, g.vars.map((x) => x.path)])).toEqual([["触发", ["trigger.mode", "trigger.slope"]]]);
    const channel = [{ label: "垂直", controls: [{ state: "scale", when: {} }, { state: "offset", when: {} }], when: {} }];
    expect(panelSections(channel, vars, (x) => x.path !== "ch3.offset", 3)[0].vars.map((x) => x.path)).toEqual(["ch3.scale"]);
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
        for (const x of main) expect(hasLabel(x) || summary.has(withoutChannel(x.path))).toBe(true);
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

/** `ch2.scale` → `scale`: the path below its channel prefix (`ch<digits>.`); others unchanged. */
function withoutChannel(path: string): string {
  const [head, ...rest] = path.split(".");
  const digits = head.slice(2);
  const isChannel = head.startsWith("ch") && digits.length > 0 && [...digits].every((c) => c >= "0" && c <= "9");
  return isChannel && rest.length ? rest.join(".") : path;
}

describe("netTitle (#1222)", () => {
  const owners = ["awg", "rc", "scope"];
  it("names a net by the instrument and part ports it joins", () => {
    expect(netTitle(["awg.OUT1", "rc.IN"], owners)).toBe("awg.OUT1 · rc.IN");
  });
  it("leaves out the points inside a wire's accessories", () => {
    expect(netTitle(["rc.OUT", "wire1.passive_probe0.IN"], owners)).toBe("rc.OUT");
    expect(netTitle(["wire1.passive_probe0.OUT", "scope.CH1"], owners)).toBe("scope.CH1");
  });
  it("is empty when only accessory points are on it", () => {
    expect(netTitle(["wire1.a.OUT", "wire1.b.IN"], owners)).toBe("");
  });
});

describe("groupName (#1251)", () => {
  it("names a group from the translation files, numbered ones with their number", () => {
    expect(groupName("power")).toBe("功率");
    expect(groupName("function3")).toBe("数学函数 3");
    expect(groupName("timebase")).toBe("时基");
    expect(groupName("nothing_like_this")).toBeNull();
  });
});

describe("distinctOptions (#1252)", () => {
  const input = { options: ["DC50", "DCFifty"], option_labels: { DC50: "DC 50 Ω", DCFifty: "DC 50 Ω" } };
  it("leaves out an alias that shows as the same word", () => {
    expect(distinctOptions({ ...input, value: "DC50" })).toEqual(["DC50"]);
  });
  it("keeps the alias when it is the current value", () => {
    expect(distinctOptions({ ...input, value: "DCFifty" })).toEqual(["DC50", "DCFifty"]);
  });
  it("keeps options whose words differ", () => {
    expect(distinctOptions({ options: ["SIN", "SQU"], option_labels: null, value: "SIN" })).toEqual(["SIN", "SQU"]);
  });
});

describe("instance families (#1257)", () => {
  const v = (path: string) => ({ path, channel: null, type: "enum", value: "" }) as unknown as StateVarInfo;
  it("folds a family of numbered settings into one block per family, the instances in number order", () => {
    const vars = [10, 2, 1, 3].flatMap((n) => [v(`display.bookmark${n}.color`), v(`display.bookmark${n}.x`)]);
    const { families, rest } = instanceFamilies([v("display.grid"), ...vars]);
    expect(families.map((f) => [f.key, f.instances])).toEqual([["display.bookmark", ["1", "2", "3", "10"]]]);
    expect(families[0].byInstance.get("2")!.map((x) => x.path)).toEqual(["display.bookmark2.color", "display.bookmark2.x"]);
    expect(rest.map((x) => x.path)).toEqual(["display.grid"]);
  });
  it("leaves a family of a few instances as rows", () => {
    const { families, rest } = instanceFamilies([v("trigger.level1.v"), v("trigger.level2.v")]);
    expect(families).toEqual([]);
    expect(rest).toHaveLength(2);
  });
});

describe("automatic and unset values (#1257)", () => {
  const num = (extra: Partial<StateVarInfo>) => ({ path: "sense.range", channel: null, type: "float", unit: "V", value: 0, ...extra }) as unknown as StateVarInfo;
  it("shows the AUTO special value and the unset value as 自动, never as 0 V", () => {
    expect(formatValue(num({ special: { AUTO: 0 } }))).toBe("自动");
    expect(formatValue(num({ unset: 0, unit: "HZ" }))).toBe("自动");
    expect(isAutoValue(num({ special: { AUTO: 0 }, value: 0.1 }))).toBe(false);
    expect(formatValue(num({ special: { AUTO: 0 }, value: 0.1 }))).toBe("100 mV");
    expect(formatValue(num({ value: 0 }))).toBe("0 V"); // a real 0 stays a number
  });
  it("writes a unit word after its prefix: an amplitude in Vpp", () => {
    expect(formatValue(num({ display_unit: "Vpp", value: 0.5 }))).toBe("500 mVpp");
    expect(formatValue(num({ display_unit: "Vpp", value: 1 }))).toBe("1 Vpp");
    expect(formatValue(num({ display_unit: "%", value: 50 }))).toBe("50%");
    expect(formatValue(num({ display_unit: "%", value: 0.001 }))).toBe("0.001%"); // no SI prefix on a sign (#1267)
    expect(formatValue(num({ display_unit: "%", value: 99.999 }))).toBe("99.999%");
  });
});

describe("autoBottomTab (#1360)", () => {
  it("opens on the first tab with something to read", () => {
    expect(autoBottomTab({ comms: false, events: true, problems: true })).toBe("events");
    expect(autoBottomTab({ comms: true, events: true, problems: true })).toBe("comms");
    expect(autoBottomTab({ comms: false, events: false, problems: true })).toBe("problems");
  });
});

describe("fieldsShown (#1580)", () => {
  it("shows whole pages, and enough of them to include a located field", () => {
    expect(fieldsShown(2000, 1)).toBe(FIELD_PAGE);
    expect(fieldsShown(10, 1)).toBe(10);
    expect(fieldsShown(2000, 2)).toBe(2 * FIELD_PAGE);
    expect(fieldsShown(2000, 1, 3 * FIELD_PAGE + 5)).toBe(4 * FIELD_PAGE);
    expect(fieldsShown(2000, 1, 5)).toBe(FIELD_PAGE);
  });
});
