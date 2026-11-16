import { describe, expect, it } from "vitest";
import {
  addComponent,
  autoLayout,
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
};

const doc = (over: Partial<ComponentDoc>): ComponentDoc =>
  ({ type: "x", category: "internal", ports: [], params: [], operations: [], refs: [], ...over }) as ComponentDoc;

const acqDoc = doc({
  type: "scope_acquisition",
  port_pattern: "in\\d+",
  params: [{ name: "depth", default: 14000, doc: null, unit: null }],
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

  it("finds the component a diagnostic points at", () => {
    expect(diagnosticNode(scope, "/components/1/params/depth")).toBe(nodeId("instrument", "acq"));
    expect(diagnosticNode(scope, "/channel_components/0")).toBe(nodeId("channel", "inp"));
    expect(diagnosticNode(scope, "/commands/2")).toBeNull();
    expect(diagnosticNode(scope, null)).toBeNull();
  });
});
