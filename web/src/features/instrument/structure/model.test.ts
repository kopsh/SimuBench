import { describe, expect, it } from "vitest";
import {
  EMPTY_HISTORY,
  componentName,
  recordEdit,
  redo,
  renameComponent,
  renameInLayout,
  undo,
  addCommand,
  addComponent,
  addSetting,
  boundSetting,
  commandHeader,
  commandSettingChoices,
  commandTarget,
  needsChannelSuffix,
  operationChoices,
  removeCommand,
  removeSetting,
  settingChoices,
  settingName,
  settingUses,
  updateCommand,
  updateSetting,
  autoLayout,
  accessPatch,
  commandAccess,
  formatNumber,
  isTable,
  libraryGroups,
  literalText,
  matches,
  parseNumber,
  portSignature,
  removeTableRow,
  setTableRow,
  settingUsers,
  tableKey,
  followedSettings,
  followers,
  refLinks,
  COLUMN,
  NODE_WIDTH,
  nodeHeight,
  canWire,
  connect,
  diagnosticNode,
  freeName,
  nodeId,
  paramKind,
  paramNames,
  panelPortName,
  parseLiteral,
  patternPrefixes,
  portsOf,
  removalCounts,
  removeComponent,
  removePanelPort,
  removeWire,
  setPanelPort,
  setParam,
  wiresOf,
  type ComponentDoc,
  type Structure,
} from "./model";

const empty: Structure = {
  components: [],
  channel_components: [],
  wiring: { instrument: [], channel: [] },
  ports: { instrument: {}, channel: {} },
  state: {},
  channel_state: {},
  commands: [],
};

/** A small scope: a channel input and front end, a shared ADC that takes every channel. */
const scope: Structure = {
  components: [
    { name: "adc", type: "scope_adc", params: {} },
    { name: "acq", type: "scope_acquisition", params: { adc: "adc", depth: 1000 } },
  ],
  channel_components: [
    { name: "inp", type: "input_port", params: { z_in: "$ch.impedance" } },
    { name: "front", type: "scope_vertical", params: { bandwidth: { from: "$ch.bwlimit", map: { ON: 2e7 } } } },
  ],
  wiring: { instrument: [["ch{n}.front.out", "adc.in{n}"]], channel: [["inp.out", "front.in"]] },
  ports: { instrument: {}, channel: { "CH{n}": "inp.pin" } },
  state: { depth: { type: "int", default: 1000 } },
  channel_state: { impedance: { type: "enum", options: ["ONEMeg", "FIFTy"], default: "ONEMeg" }, bwlimit: { type: "bool", default: false } },
  commands: [
    { header: ":CHANnel<ch>:IMPedance", state: "ch.impedance" },
    { header: ":DIGitize", op: "acq.digitize", query: false },
  ],
};

const doc = (over: Partial<ComponentDoc>): ComponentDoc =>
  ({ type: "x", category: "internal", ports: [], params: [], operations: [], refs: [], ...over }) as ComponentDoc;

const acqDoc = doc({
  type: "scope_acquisition",
  port_pattern: "in\\d+",
  params: [{ name: "depth", default: 14000, doc: null, unit: null, minimum: null, maximum: null, min_open: false, max_open: false }],
  refs: [{ param: "adc", scope: "instrument", optional: false, target: "scope_adc" }],
});

describe("wires", () => {
  it("resolves channel ends written with ch{n}. at instrument level", () => {
    const wires = wiresOf(scope);
    expect(wires).toHaveLength(2);
    expect(wires[0].from).toEqual({ scope: "channel", name: "front", port: "out" });
    expect(wires[0].to).toEqual({ scope: "instrument", name: "adc", port: "in{n}" });
    expect(wires[1].list).toBe("channel");
  });

  it("writes a wire between two channel components inside the channel", () => {
    const s = addComponent(scope, "scope_probe", "channel").structure;
    const next = connect(s, { scope: "channel", name: "front", port: "probe" }, { scope: "channel", name: "scope_probe", port: "in" });
    expect(next?.wiring.channel.at(-1)).toEqual(["front.probe", "scope_probe.in"]);
  });

  it("writes a channel-to-instrument wire at instrument level with the channel prefix", () => {
    const next = connect(scope, { scope: "channel", name: "inp", port: "mon" }, { scope: "instrument", name: "acq", port: "in1" });
    expect(next?.wiring.instrument.at(-1)).toEqual(["ch{n}.inp.mon", "acq.in1"]);
  });

  it("refuses a second wire into the same input and a wire to itself", () => {
    expect(connect(scope, { scope: "channel", name: "inp", port: "x" }, { scope: "channel", name: "front", port: "in" })).toBeNull();
    expect(connect(scope, { scope: "instrument", name: "adc", port: "out1" }, { scope: "instrument", name: "adc", port: "in2" })).toBeNull();
  });

  it("removes a wire by its list and index", () => {
    expect(removeWire(scope, "channel", 0).wiring.channel).toEqual([]);
  });
});

describe("components", () => {
  it("names a new component after its type, numbered when taken", () => {
    expect(freeName(scope, "output_stage")).toBe("output_stage");
    const once = addComponent(empty, "output_stage", "instrument").structure;
    expect(addComponent(once, "output_stage", "channel").name).toBe("output_stage2");
  });

  it("deletes a component with its wires, its panel ports and the refs that named it", () => {
    const docs = new Map([["scope_acquisition", acqDoc]]);
    expect(removalCounts(scope, "channel", "inp")).toEqual({ wires: 1, ports: 1 });
    const s = removeComponent(scope, "instrument", "adc", docs);
    expect(s.components.map((c) => c.name)).toEqual(["acq"]);
    expect(s.wiring.instrument).toEqual([]);
    expect(s.components[0].params).toEqual({ depth: 1000 });
    const t = removeComponent(scope, "channel", "inp", docs);
    expect(t.ports.channel).toEqual({});
    expect(t.wiring.channel).toEqual([]);
  });
});

describe("parameters", () => {
  it("tells a literal, a setting and a refs parameter apart", () => {
    const acq = scope.components[1];
    expect(paramKind(acq, acqDoc, "depth")).toEqual({ kind: "literal", value: 1000, set: true });
    expect(paramKind(acq, acqDoc, "adc")).toMatchObject({ kind: "ref", value: "adc", target: "scope_adc" });
    expect(paramKind(scope.channel_components[0], undefined, "z_in")).toEqual({ kind: "setting", setting: "ch.impedance" });
    expect(paramKind(scope.channel_components[1], undefined, "bandwidth")).toEqual({ kind: "setting", setting: "ch.bwlimit" });
  });

  it("shows the default of a parameter the definition does not write", () => {
    const bare = { name: "acq", type: "scope_acquisition", params: {} };
    expect(paramKind(bare, acqDoc, "depth")).toEqual({ kind: "literal", value: 14000, set: false });
    expect(paramNames(acq(), acqDoc)).toEqual(["depth", "adc", "extra"]);
  });

  it("sets and clears a parameter", () => {
    const s = setParam(scope, "instrument", "acq", "depth", 2000);
    expect(s.components[1].params).toEqual({ adc: "adc", depth: 2000 });
    expect(setParam(s, "instrument", "acq", "depth", undefined).components[1].params).toEqual({ adc: "adc" });
  });

  it("parses a typed literal like the default", () => {
    expect(parseLiteral("2.5e6", 1)).toEqual({ ok: true, value: 2.5e6 });
    expect(parseLiteral("abc", 1)).toEqual({ ok: false });
    expect(parseLiteral("true", false)).toEqual({ ok: true, value: true });
    expect(parseLiteral("", 1)).toEqual({ ok: true, value: undefined });
    expect(parseLiteral('{"a": 1}', {})).toEqual({ ok: true, value: { a: 1 } });
    expect(parseLiteral("bessel4", "gauss")).toEqual({ ok: true, value: "bessel4" });
  });
});

function acq() {
  return { name: "acq", type: "scope_acquisition", params: { adc: "adc", depth: 1, extra: 2 } };
}

describe("ports", () => {
  it("reads the prefixes of a numbered port pattern", () => {
    expect(patternPrefixes("in\\d+")).toEqual(["in"]);
    expect(patternPrefixes("(in|out)\\d+")).toEqual(["in", "out"]);
    expect(patternPrefixes("ch\\d{3}[HL]")).toEqual([]);
    expect(patternPrefixes(null)).toEqual([]);
  });

  it("offers the next free port of a pattern after the wired ones", () => {
    const s = connect(scope, { scope: "channel", name: "front", port: "aux" }, { scope: "instrument", name: "acq", port: "in1" })!;
    const rows = portsOf(s, "instrument", "acq", acqDoc).map((r) => [r.name, !!r.extra]);
    expect(rows).toEqual([
      ["in1", false],
      ["in2", true],
    ]);
  });

  it("wires an output to an input of the same medium, never an electrical pin", () => {
    const out = { name: "out", direction: "out", medium: "signal" };
    expect(canWire(out, { name: "in", direction: "in", medium: "signal" })).toBe(true);
    expect(canWire(out, { name: "in", direction: "in", medium: "samples" })).toBe(false);
    expect(canWire(out, { name: "in1", direction: "in", medium: "any" })).toBe(true);
    expect(canWire({ name: "pin", direction: "out", medium: "electrical" }, { name: "in", direction: "in", medium: "signal" })).toBe(false);
    expect(canWire({ name: "in", direction: "in", medium: "signal" }, out)).toBe(false);
  });
});

describe("panel ports", () => {
  it("names a channel port with {n}", () => {
    expect(panelPortName("CH", "channel")).toBe("CH{n}");
    expect(panelPortName("OUT", "instrument")).toBe("OUT");
    expect(panelPortName("  ", "instrument")).toBeNull();
    expect(panelPortName("A B", "instrument")).toBeNull();
  });

  it("adds and removes a panel port", () => {
    const s = setPanelPort(empty, "instrument", "OUT", "out.pin");
    expect(s.ports.instrument).toEqual({ OUT: "out.pin" });
    expect(removePanelPort(s, "instrument", "OUT").ports.instrument).toEqual({});
  });
});

describe("layout and diagnostics", () => {
  it("puts a component one column right of what feeds it, the channel band on top", () => {
    const at = autoLayout(scope);
    expect(at[nodeId("channel", "front")].x).toBeGreaterThan(at[nodeId("channel", "inp")].x);
    expect(at[nodeId("instrument", "adc")].x).toBeGreaterThan(at[nodeId("channel", "front")].x);
    expect(at[nodeId("instrument", "adc")].y).toBeGreaterThan(at[nodeId("channel", "front")].y);
  });

  it("keeps a position the user dragged to", () => {
    const id = nodeId("instrument", "adc");
    expect(autoLayout(scope, { [id]: { x: 5, y: 7 } })[id]).toEqual({ x: 5, y: 7 });
  });

  it("puts the components on no signal chain in a grid below it, a reference beside its target (#1212)", () => {
    // A DMM: input → path on a chain; meter, its calibration store, a trigger model and a buffer on none.
    const storeDoc = doc({ type: "dmm_adc", refs: [{ param: "store", scope: "instrument", optional: false, target: null }] });
    const dmm: Structure = {
      ...empty,
      components: [
        { name: "trig", type: "trigger_model", params: {} },
        { name: "input", type: "dmm_input_terminal", params: {} },
        { name: "meter", type: "dmm_adc", params: { store: "cal" } },
        { name: "path", type: "signal_conditioning", params: {} },
        { name: "mem", type: "reading_buffer", params: {} },
        { name: "cal", type: "calibration_store", params: {} },
      ],
      wiring: { instrument: [["input.out", "path.in"]], channel: [] },
    };
    const at = autoLayout(dmm, {}, new Map([["dmm_adc", storeDoc]]));
    const p = (n: string) => at[nodeId("instrument", n)];
    expect(p("input")).toEqual({ x: 0, y: 0 });
    expect(p("path")).toEqual({ x: COLUMN, y: 0 });
    // the other four below the chain, not stacked in one column
    const loose = ["trig", "meter", "cal", "mem"].map(p);
    expect(Math.min(...loose.map((q) => q.y))).toBeGreaterThan(p("input").y);
    expect(new Set(loose.map((q) => q.x)).size).toBeGreaterThan(1);
    // the store follows the meter that names it
    expect(p("cal").x - p("meter").x).toBe(COLUMN);
    expect(p("cal").y).toBe(p("meter").y);
    // no two components in the same place
    expect(new Set(Object.values(at).map((q) => `${q.x},${q.y}`)).size).toBe(6);
    // no channel components: the instrument starts at the top
    expect(Math.min(...Object.values(at).map((q) => q.y))).toBe(0);
  });

  it("never overlaps two blocks, however many ports and followed settings they have (#1216)", () => {
    // Tall blocks: an acquisition with many numbered inputs, components following many settings,
    // a panel port — on chains, in the grid and in the channel band.
    const many = (prefix: string, n: number) => Object.fromEntries(Array.from({ length: n }, (_, i) => [`p${i}`, "$" + prefix + i]));
    const tall: Structure = {
      ...scope,
      components: [
        ...scope.components.map((c) => ({ ...c, params: { ...c.params, ...many("a", 5) } })),
        { name: "acq2", type: "scope_acquisition", params: many("b", 4) },
        { name: "x1", type: "x", params: many("c", 3) },
        { name: "x2", type: "x", params: many("d", 6) },
        { name: "x3", type: "x", params: {} },
        { name: "x4", type: "x", params: many("e", 2) },
      ],
      channel_components: [
        ...scope.channel_components.map((c) => ({ ...c, params: { ...c.params, ...many("f", 4) } })),
        { name: "cx", type: "x", params: many("g", 5) },
      ],
      wiring: {
        instrument: [
          ...scope.wiring.instrument,
          ["ch{n}.front.out", "acq.in1"],
          ["ch{n}.front.out", "acq.in2"],
          ["ch{n}.front.out", "acq.in3"],
          ["acq.out", "acq2.in1"],
        ],
        channel: scope.wiring.channel,
      },
      ports: { instrument: { MON: "acq.mon" }, channel: scope.ports.channel },
    };
    const docs = new Map<string, ComponentDoc>([["scope_acquisition", acqDoc]]);
    const at = autoLayout(tall, {}, docs);
    const boxes = (["channel", "instrument"] as const).flatMap((sc) =>
      (sc === "channel" ? tall.channel_components : tall.components).map((c) => {
        const p = at[nodeId(sc, c.name)];
        return { name: c.name, x: p.x, y: p.y, w: NODE_WIDTH, h: nodeHeight(tall, sc, c, docs) };
      }),
    );
    expect(boxes).toHaveLength(10);
    expect(Math.max(...boxes.map((b) => b.h))).toBeGreaterThan(150); // taller than the old fixed row
    const overlapping: string[] = [];
    for (let i = 0; i < boxes.length; i++)
      for (let j = i + 1; j < boxes.length; j++) {
        const a = boxes[i];
        const b = boxes[j];
        if (a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h) overlapping.push(`${a.name}/${b.name}`);
      }
    expect(overlapping).toEqual([]);
  });

  it("finds the component a diagnostic points at", () => {
    expect(diagnosticNode(scope, "/components/1/params/depth")).toBe(nodeId("instrument", "acq"));
    expect(diagnosticNode(scope, "/channel_components/0")).toBe(nodeId("channel", "inp"));
    expect(diagnosticNode(scope, "/commands/2")).toBeNull();
    expect(diagnosticNode(scope, null)).toBeNull();
  });
});

describe("settings (#1206)", () => {
  it("adds a setting with a default of its type and refuses a taken name", () => {
    const s = addSetting(scope, "instrument", "level", "float")!;
    expect(s.state.level).toEqual({ type: "float", default: 0 });
    expect(addSetting(s, "instrument", "level", "bool")).toBeNull();
    expect(addSetting(scope, "channel", "on", "enum")!.channel_state.on).toEqual({ type: "enum", options: ["A", "B"], default: "A" });
  });

  it("checks a setting name", () => {
    expect(settingName(" out.level ")).toBe("out.level");
    expect(settingName("ch.level")).toBeNull();
    expect(settingName("1x")).toBeNull();
    expect(settingName("a b")).toBeNull();
  });

  it("edits fields, and a new type drops what only the old one had", () => {
    let s = updateSetting(scope, "channel", "impedance", { label: "阻抗" });
    expect(s.channel_state.impedance).toMatchObject({ label: "阻抗", options: ["ONEMeg", "FIFTy"] });
    s = updateSetting(s, "channel", "impedance", { type: "float" });
    expect(s.channel_state.impedance).toEqual({ type: "float", default: 0, label: "阻抗" });
    s = updateSetting(s, "channel", "impedance", { label: undefined, unit: "OHM" });
    expect(s.channel_state.impedance).toEqual({ type: "float", default: 0, unit: "OHM" });
  });

  it("counts the parameters and commands that name a setting, and deletes it", () => {
    const bound = { ...scope, channel_components: [{ name: "inp", type: "input_port", params: { z_in: "$ch.impedance" } }] };
    expect(settingUses(bound, "channel", "impedance")).toEqual({ params: 1, commands: 1 });
    expect(settingUses(bound, "instrument", "depth")).toEqual({ params: 0, commands: 0 });
    expect(removeSetting(bound, "channel", "impedance").channel_state).not.toHaveProperty("impedance");
  });

  it("offers a channel component its channel's settings and the instrument's", () => {
    expect(settingChoices(scope, "channel")).toEqual(["ch.impedance", "ch.bwlimit", "depth"]);
    expect(settingChoices(scope, "instrument")).toEqual(["depth"]);
    expect(boundSetting("$ch.impedance")).toBe("ch.impedance");
    expect(boundSetting({ from: "$ch.bwlimit", map: {} })).toBe("ch.bwlimit");
    expect(boundSetting(3)).toBeNull();
  });
});

describe("commands (#1206)", () => {
  const docs = new Map<string, ComponentDoc>([
    ["scope_acquisition", doc({ operations: [{ name: "digitize", params: [], doc: null }] })],
    ["input_port", doc({ operations: [{ name: "probe_comp", params: [], doc: null }] })],
  ]);

  it("lists what a command can bind to", () => {
    expect(commandSettingChoices(scope)).toEqual(["depth", "ch.impedance", "ch.bwlimit"]);
    expect(operationChoices(scope, docs)).toEqual(["acq.digitize", "ch.inp.probe_comp"]);
  });

  it("adds a setting command and an operation command, not a header twice", () => {
    const s = addCommand(scope, ":ACQuire:DEPTh", "state", "depth")!;
    expect(s.commands.at(-1)).toEqual({ header: ":ACQuire:DEPTh", state: "depth" });
    expect(addCommand(s, ":ACQuire:DEPTh", "state", "depth")).toBeNull();
    expect(addCommand(scope, ":RUN", "op", "acq.digitize")!.commands.at(-1)).toEqual({ header: ":RUN", op: "acq.digitize", query: false });
  });

  it("rebinds a command from an operation to a setting and keeps its other fields", () => {
    const s = updateCommand(scope, 1, { state: "depth" });
    expect(s.commands[1]).toEqual({ header: ":DIGitize", state: "depth", query: false });
    expect(commandTarget(s.commands[1])).toEqual({ kind: "state", target: "depth" });
    expect(updateCommand(s, 1, { query: undefined }).commands[1]).toEqual({ header: ":DIGitize", state: "depth" });
    expect(removeCommand(s, 0).commands).toHaveLength(1);
  });

  it("checks a header and points out a channel command without <ch>", () => {
    expect(commandHeader(" :LEVel ")).toBe(":LEVel");
    expect(commandHeader(":A B")).toBeNull();
    expect(needsChannelSuffix({ header: ":CHANnel:SCALe", state: "ch.scale" })).toBe(true);
    expect(needsChannelSuffix({ header: ":CHANnel<ch>:SCALe", state: "ch.scale" })).toBe(false);
    expect(needsChannelSuffix({ header: ":RUN", op: "acq.run" })).toBe(false);
  });
});

describe("rename (#1207)", () => {
  const docs = new Map<string, ComponentDoc>([["scope_acquisition", acqDoc]]);

  it("checks a new name", () => {
    expect(componentName(scope, " amp ")).toBe("amp");
    expect(componentName(scope, "a.b")).toBeNull();
    expect(componentName(scope, "front")).toBeNull();
    expect(componentName(scope, "front", "front")).toBe("front");
  });

  it("renames a channel component in its wires, panel ports and commands", () => {
    const s = renameComponent({ ...scope, commands: [{ header: ":X", op: "ch.inp.probe_comp" }] }, "channel", "inp", "input", docs);
    expect(s.channel_components[0].name).toBe("input");
    expect(s.wiring.channel).toEqual([["input.out", "front.in"]]);
    expect(s.ports.channel).toEqual({ "CH{n}": "input.pin" });
    expect(s.commands[0].op).toBe("ch.input.probe_comp");
  });

  it("renames an instrument component in channel-to-instrument wires and in refs", () => {
    const s = renameComponent(scope, "instrument", "adc", "adc_bank", docs);
    expect(s.wiring.instrument).toEqual([["ch{n}.front.out", "adc_bank.in{n}"]]);
    expect(s.components[1].params).toEqual({ adc: "adc_bank", depth: 1000 });
    expect(s.commands).toEqual(scope.commands);
  });

  it("moves a renamed node's position", () => {
    expect(renameInLayout({ "i:a": { x: 1, y: 2 } }, "i:a", "i:b")).toEqual({ "i:b": { x: 1, y: 2 } });
  });
});

describe("undo (#1207)", () => {
  it("steps back and forward, and a new edit clears what was ahead", () => {
    const a = empty;
    const b = addComponent(a, "x", "instrument").structure;
    const c = addComponent(b, "y", "instrument").structure;
    let h = recordEdit(recordEdit(EMPTY_HISTORY, a), b);
    const back = undo(h, c)!;
    expect(back.structure).toBe(b);
    const forward = redo(back.history, back.structure)!;
    expect(forward.structure).toBe(c);
    h = recordEdit(back.history, b);
    expect(h.future).toEqual([]);
    expect(undo(EMPTY_HISTORY, a)).toBeNull();
    expect(redo(EMPTY_HISTORY, a)).toBeNull();
    expect(recordEdit(EMPTY_HISTORY, a, 1).past).toHaveLength(1);
  });
});

describe("references and settings on the canvas (#1212)", () => {
  const docs = new Map<string, ComponentDoc>([["scope_acquisition", acqDoc]]);

  it("lists a reference whose target exists, as node ids", () => {
    expect(refLinks(scope, docs)).toEqual([{ from: nodeId("instrument", "acq"), to: nodeId("instrument", "adc"), param: "adc" }]);
    const dangling = setParam(scope, "instrument", "acq", "adc", "nothing");
    expect(refLinks(dangling, docs)).toEqual([]);
    expect(refLinks(scope, new Map())).toEqual([]);
  });

  it("names the settings a component follows, bound and mapped", () => {
    expect(followedSettings(scope.channel_components[0])).toEqual([{ setting: "ch.impedance", params: ["z_in"] }]);
    expect(followedSettings(scope.channel_components[1])).toEqual([{ setting: "ch.bwlimit", params: ["bandwidth"] }]);
    expect(followedSettings(scope.components[1])).toEqual([]);
  });

  it("finds the components that follow a setting", () => {
    expect(followers(scope, "ch.impedance")).toEqual([nodeId("channel", "inp")]);
    expect(followers(scope, "depth")).toEqual([]);
    const both = setParam(scope, "instrument", "acq", "depth", "$depth");
    expect(followers(both, "depth")).toEqual([nodeId("instrument", "acq")]);
  });
});

describe("numbers, tables, the library and setting users (#1214)", () => {
  it("reads a number with an SI prefix, m for milli and M for mega", () => {
    expect(parseNumber("10M")).toBe(1e7);
    expect(parseNumber("1.5k")).toBe(1500);
    expect(parseNumber("100u")).toBe(1e-4);
    expect(parseNumber("2m")).toBe(0.002);
    expect(parseNumber("4.7 n")).toBe(4.7e-9);
    expect(parseNumber("2.5e6")).toBe(2.5e6);
    expect(parseNumber("1e3k")).toBe(1e6);
    expect(parseNumber("10X")).toBeNull();
    expect(parseNumber("")).toBeNull();
    expect(parseLiteral("10M", 50)).toEqual({ ok: true, value: 1e7 });
  });

  it("writes a number with a prefix only where it reads back the same", () => {
    expect(formatNumber(1e7)).toBe("10M");
    expect(formatNumber(1e10)).toBe("10G");
    expect(formatNumber(1e-4)).toBe("100u");
    expect(formatNumber(1.2)).toBe("1.2");
    expect(formatNumber(0)).toBe("0");
    expect(formatNumber(9.9e37)).toBe("9.9e+37");
    expect(literalText(1e7)).toBe("10M");
    expect(literalText({ a: 1 })).toBe('{"a":1}');
  });

  it("edits a table row by row, keys kept in place", () => {
    const t = { "1": 0.1, "3": 0.1 };
    expect(isTable(t)).toBe(true);
    expect(isTable([1, 2])).toBe(false);
    expect(isTable({ a: { b: 1 } })).toBe(false);
    expect(Object.keys(setTableRow(t, "1", 0.2, "2"))).toEqual(["2", "3"]);
    expect(setTableRow(t, "10", 0.5)).toEqual({ "1": 0.1, "3": 0.1, "10": 0.5 });
    expect(removeTableRow(t, "1")).toEqual({ "3": 0.1 });
    expect(tableKey(" 10k ")).toBe("10000");
    expect(tableKey("DC")).toBe("DC");
    expect(tableKey(" ")).toBeNull();
  });

  it("groups the library by family and filters it", () => {
    const docs = [
      doc({ type: "trigger_model", group: "trigger" }),
      doc({ type: "output_stage", group: "signal", doc: "A driven output" }),
      doc({ type: "input_port", group: "signal", label: "输入端口", label_en: "Input port" }),
      doc({ type: "resistor", category: "part", group: "other" }),
    ];
    const all = libraryGroups(docs, "").groups;
    expect(all.map((g) => g.group)).toEqual(["signal", "trigger"]);
    expect(all[0].items.map((d) => d.type)).toEqual(["output_stage", "input_port"]);
    const found = (q: string) => libraryGroups(docs, q).groups.flatMap((g) => g.items.map((d) => d.type));
    expect(found("driven")).toEqual(["output_stage"]);
    // the names in both languages count as well as the type (#1251)
    expect(found("输入")).toEqual(["input_port"]);
    expect(found("input port")).toEqual(["input_port"]);
  });

  it("puts the class's own types first and folds the others until asked or searched (P2-4)", () => {
    const docs = [
      doc({ type: "scope_vertical", group: "scope", classes: ["oscilloscope"] }),
      doc({ type: "input_port", group: "signal", classes: ["oscilloscope", "lockin"] }),
      doc({ type: "dmm_adc", group: "measure", classes: ["multimeter"] }),
    ];
    const folded = libraryGroups(docs, "", "oscilloscope", false);
    expect(folded.groups.map((g) => [g.group, g.items.map((d) => d.type)])).toEqual([["own", ["scope_vertical", "input_port"]]]);
    expect(folded.hidden).toBe(1);
    expect(libraryGroups(docs, "", "oscilloscope", true).groups.map((g) => g.group)).toEqual(["own", "measure"]);
    expect(libraryGroups(docs, "dmm", "oscilloscope", false).groups.map((g) => g.group)).toEqual(["measure"]);
  });

  it("writes a type's ports in one line", () => {
    const d = doc({
      ports: [
        { name: "in", direction: "in", medium: "voltage" },
        { name: "out", direction: "out", medium: "voltage" },
        { name: "pin", direction: "out", medium: "electrical" },
      ] as ComponentDoc["ports"],
    });
    expect(portSignature(d)).toBe("in → out  ⊡ pin");
    expect(portSignature(acqDoc)).toBe("in1… → —");
    expect(portSignature(doc({}))).toBe("");
  });

  it("names who uses a setting", () => {
    expect(settingUsers(scope, "channel", "impedance")).toEqual({ components: ["inp"], commands: [":CHANnel<ch>:IMPedance"] });
    expect(settingUsers(scope, "instrument", "depth")).toEqual({ components: [], commands: [] });
    expect(matches("imp", "ch.impedance")).toBe(true);
    expect(matches("", "x")).toBe(true);
    expect(matches("zz", "x", null)).toBe(false);
  });
});

describe("command access (#1214)", () => {
  it("reads three uses from set and query, none when both are off", () => {
    expect(commandAccess({ header: "A" })).toBe("both");
    expect(commandAccess({ header: "A", query: false })).toBe("set");
    expect(commandAccess({ header: "A", set: false })).toBe("query");
    expect(commandAccess({ header: "A", set: false, query: false })).toBeNull();
  });

  it("writes only what differs from the default", () => {
    const s = addCommand(scope, ":X", "state", "depth")!;
    const i = s.commands.length - 1;
    const only = updateCommand(s, i, accessPatch("query"));
    expect(only.commands[i]).toEqual({ header: ":X", state: "depth", set: false });
    const both = updateCommand(only, i, accessPatch("both"));
    expect(both.commands[i]).toEqual({ header: ":X", state: "depth" });
    expect(commandAccess(updateCommand(both, i, accessPatch("set")).commands[i])).toBe("set");
  });
});
