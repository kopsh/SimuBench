import { describe, expect, it } from "vitest";
import type { CommandSpec, DefinitionCoverage, InstrumentInfo, StateVarInfo } from "@/api/client";
import { commandRows, deviationLimit, groupCommands, normHeader, panelView, paramFacts, specRows, topNode } from "./model";

const cmd = (header: string, extra: Partial<CommandSpec> = {}): CommandSpec => ({
  header,
  state: "x.y",
  set: true,
  query: true,
  params: [],
  args: {},
  factor: 1,
  dangerous: false,
  ...extra,
});

const coverage = (entries: [string, "implemented" | "accepted" | "not_modeled"][]): DefinitionCoverage => ({
  model: "m",
  stats: { implemented: 0, accepted: 0, not_modeled: 0 },
  entries: entries.map(([header, level]) => ({ section: "S", header, level, note: null })),
});

describe("command list", () => {
  it("merges the coverage table with the definition's commands, normalized as the checker does", () => {
    const rows = commandRows(
      [cmd("[:SENSe]:VOLTage[:DC]:NPLCycles"), cmd("[:SENSe]:VOLTage:AC:RANGe")],
      coverage([
        ["[:sense]:VOLTage[:dc]:NPLCycles", "implemented"],
        ["[:SENSe]:VOLTage:AC:RANGe", "accepted"],
        ["SYSTem:COMMunicate:LAN:IPADdress", "not_modeled"],
      ]),
    );
    expect(rows.map((r) => [r.header, r.level])).toEqual([
      ["[:SENSe]:VOLTage[:DC]:NPLCycles", "implemented"],
      ["[:SENSe]:VOLTage:AC:RANGe", "accepted"],
      ["SYSTem:COMMunicate:LAN:IPADdress", "not_modeled"],
    ]);
    expect(rows[2].command).toBeNull(); // the manual lists what this definition does not model
  });

  it("keeps every command that shares a header, and lists commands without coverage as declared", () => {
    const rows = commandRows([cmd("A"), cmd("A", { when: { x: "y" } })], null);
    expect(rows).toHaveLength(2);
    expect(rows.every((r) => r.level === null && r.command !== null)).toBe(true);
  });

  it("groups by the command tree's first node and folds the common commands into one group", () => {
    expect(topNode("[:SENSe]:VOLTage[:DC]")).toBe("SENSE");
    expect(topNode(":CALibration:COUNt")).toBe("CALIBRATION");
    expect(topNode("*IDN?")).toBe("*");
    expect(topNode("SYSTem:ERRor?")).toBe("SYSTEM");
    const groups = groupCommands(commandRows([cmd("*IDN?"), cmd("*RST"), cmd(":TRIGger:MODe")], null));
    expect(groups.map((g) => [g.node, g.rows.length])).toEqual([
      ["*", 2],
      ["TRIGGER", 1],
    ]);
  });

  it("normalizes as the coverage check does", () => {
    expect(normHeader(" [:SENSe]<ch> :VOLTage ")).toBe("[:SENSE]<>:VOLTAGE");
    expect(normHeader(":sense:volt")).toBe("SENSE:VOLT");
  });

  it("describes parameters and deviations in the instrument's language", () => {
    expect(paramFacts({ name: "range", type: "float", unit: "V", min: 0.1, max: 1000, optional: true } as never)).toBe(
      "float · V · 100 mV … 1000 V · 可选",
    );
    expect(deviationLimit(2e-5, "V")).toBe("±20 µV");
    expect(deviationLimit({ DC: 1e-5, AC: 2e-4 }, "V")).toBe("DC: ±10 µV；AC: ±200 µV");
  });

  it("flattens specifications one level", () => {
    expect(specRows({ digits: "6½", input: { DC: "10 MΩ", AC: "1 MΩ" }, max: 1e9 })).toEqual([
      { key: "digits", value: "6½" },
      { key: "input.DC", value: "10 MΩ" },
      { key: "input.AC", value: "1 MΩ" },
      { key: "max", value: "1000000000" },
    ]);
  });
});

describe("panel view", () => {
  const state = (path: string, extra: Partial<StateVarInfo>): StateVarInfo => ({
    path,
    type: "float",
    unit: null,
    min: null,
    max: null,
    options: null,
    option_labels: null,
    display_unit: null,
    label: null,
    label_en: null,
    default: null,
    rst: null,
    ladder: null,
    special: null,
    not_modeled: null,
    replies: null,
    readonly: false,
    electrical: false,
    dangerous: false,
    channel: null,
    value: 0,
    ...extra,
  });
  const inst = (extra: Record<string, unknown>): InstrumentInfo =>
    ({
      display: { actions: {}, channel_summary: [], panel: null, panel_source: null, summary: [], template: null },
      state: [],
      channels: 1,
      ...extra,
    }) as unknown as InstrumentInfo;

  it("reads the declared panel's groups and their key labels", () => {
    const view = panelView(
      inst({
        display: {
          panel: {
            readouts: [{ reading: "meter.DC", label: "直流", label_en: "DC", unit: "V", digits: 7, when: {} }],
            groups: [
              {
                label: "量程",
                label_en: "Range",
                controls: [
                  {
                    state: "sense.range",
                    control: "buttons",
                    label: null,
                    label_en: null,
                    keys: [
                      { value: 1, label: "1 V", label_en: null, sets: {} },
                      { value: 10, label: "10 V", label_en: null, sets: {} },
                    ],
                    step: null,
                    unit: null,
                  },
                ],
              },
            ],
            softkeys: [],
          },
          panel_source: "declared",
        },
        state: [state("sense.range", { unit: "V", min: 1, max: 10 })],
      }),
    );
    expect(view.readouts).toEqual([{ label: "直流", reading: "meter.DC", unit: "V" }]);
    expect(view.groups[0].controls[0].choices).toEqual(["1 V", "10 V"]);
  });

  it("falls back to the settings themselves for a class without a panel", () => {
    const view = panelView(
      inst({
        state: [
          state("sense.nplc", { min: 0.02, max: 100, label: "积分时间", value: 10 }),
          state("sense.range_auto", { type: "bool", label: "自动量程", value: true }),
          state("sense.version", { readonly: true }),
        ],
      }),
    );
    expect(view.groups).toHaveLength(1);
    expect(view.groups[0].controls.map((c) => [c.state, c.kind, c.range])).toEqual([
      ["sense.nplc", "number", "0.02 … 100"],
      ["sense.range_auto", "switch", null],
    ]);
  });
});
