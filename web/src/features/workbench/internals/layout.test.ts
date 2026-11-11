import { describe, expect, it } from "vitest";
import type { InstrumentInternals } from "@/api/client";
import fixture from "./internals.fixture.json";
import { buildGraph } from "./layout";

/** What a wire carries when it is analog (the API fills these in). */
const ANALOG = { medium: "signal" as const, sample_rate_hz: null, format: null };

const port = (
  name: string,
  direction: string,
  medium = "signal",
  channel: number | null = null,
) => ({ name, direction, medium, channel, net: null });

// A two-channel scope: CH<n> -> inp -> front -> acq.in<n>.
const scope: InstrumentInternals = {
  instrument: "scope",
  definition_id: "sb-dso-2x",
  reference: null,
  channels: 2,
  components: [
    {
      name: "acq",
      type: "scope_acquisition",
      channel: null,
      doc: null,
      params: [],
      settings: [],
      commands: [],
      readings: [],
      ports: [port("in1", "in"), port("in2", "in")],
    },
    ...[1, 2].flatMap((n) => [
      {
        name: `ch${n}.inp`,
        type: "input_port",
        channel: n,
        doc: null,
        params: [],
        settings: [],
        commands: [],
        readings: [],
        ports: [
          port("pin", "in", "electrical", n),
          port("out", "out", "signal", n),
        ],
      },
      {
        name: `ch${n}.front`,
        type: "scope_frontend",
        channel: n,
        doc: null,
        params: [],
        settings: [],
        commands: [],
        readings: [],
        ports: [port("in", "in", "signal", n), port("out", "out", "signal", n)],
      },
    ]),
  ],
  wires: [1, 2].flatMap((n) => [
    { from: `ch${n}.inp.out`, to: `ch${n}.front.in`, ...ANALOG },
    { from: `ch${n}.front.out`, to: `acq.in${n}`, ...ANALOG },
  ]),
  refs: [],
  selects: [],
  ports: [1, 2].map((n) => ({
    name: `CH${n}`,
    target: `ch${n}.inp.pin`,
    direction: "in",
    medium: "electrical",
    channel: n,
  })),
};

describe("buildGraph", () => {
  it("lays the signal flow out left to right", () => {
    const { nodes, edges } = buildGraph(scope, new Set([1, 2]));
    const x = Object.fromEntries(nodes.map((n) => [n.id, n.x]));
    expect(x["p:CH1"]).toBeLessThan(x["c:ch1.inp"]);
    expect(x["c:ch1.inp"]).toBeLessThan(x["c:ch1.front"]);
    expect(x["c:ch1.front"]).toBeLessThan(x["c:acq"]);
    expect(edges.map((e) => e.id)).toContain("c:ch1.front/out->c:acq/in1");
    expect(edges.map((e) => e.id)).toContain("p:CH1/p->c:ch1.inp/pin");
  });

  it("draws a collapsed channel as one group with its wiring", () => {
    const { nodes, edges } = buildGraph(scope, new Set([1]));
    expect(nodes.some((n) => n.id === "c:ch2.inp")).toBe(false);
    const group = nodes.find((n) => n.id === "g:2");
    expect(group).toMatchObject({ kind: "group", count: 2 });
    const ids = edges.map((e) => e.id);
    expect(ids).toContain("p:CH2/p->g:2/in");
    expect(ids).toContain("g:2/out->c:acq/in2");
    expect(ids.some((id) => id.startsWith("g:2/out->g:2"))).toBe(false); // no self loop
  });

  it("keeps the component links an edge draws, for tracing a collapsed channel (#739)", () => {
    const { edges } = buildGraph(scope, new Set([1]));
    expect(edges.find((e) => e.id === "g:2/out->c:acq/in2")!.links).toEqual([
      "ch2.front>acq",
    ]);
    expect(edges.find((e) => e.id === "p:CH2/p->g:2/in")!.links).toEqual([
      "p:CH2>ch2.inp",
    ]);
  });

  it("does not let nodes in a column overlap", () => {
    const { nodes } = buildGraph(scope, new Set([1, 2]));
    const byColumn = new Map<number, typeof nodes>();
    for (const n of nodes) byColumn.set(n.x, [...(byColumn.get(n.x) ?? []), n]);
    for (const col of byColumn.values()) {
      const sorted = [...col].sort((a, b) => a.y - b.y);
      for (let i = 1; i < sorted.length; i++)
        expect(sorted[i].y).toBeGreaterThanOrEqual(
          sorted[i - 1].y + sorted[i - 1].height,
        );
    }
  });
});

// The same scope with what the acquisition uses and what uses it (#729), and a system block only commands drive.
const flow: InstrumentInternals = {
  ...scope,
  components: [
    ...scope.components,
    {
      name: "adc",
      type: "scope_adc",
      channel: null,
      doc: null,
      params: [],
      settings: [],
      commands: [],
      readings: [],
      ports: [],
    },
    {
      name: "timebase",
      type: "scope_timebase",
      channel: null,
      doc: null,
      params: [],
      settings: [],
      commands: [],
      readings: [],
      ports: [],
    },
    {
      name: "meas",
      type: "scope_measurements",
      channel: null,
      doc: null,
      params: [],
      settings: [],
      commands: [],
      readings: [],
      ports: [],
    },
    {
      name: "sys",
      type: "system",
      channel: null,
      doc: null,
      params: [],
      settings: [],
      commands: [],
      readings: [],
      ports: [],
    },
  ],
  refs: [
    { from: "adc", to: "acq", param: "adc" },
    { from: "timebase", to: "acq", param: "horizontal" },
    { from: "acq", to: "meas", param: "record" },
  ],
};

describe("buildGraph dependencies (#729)", () => {
  it("draws what a component uses as a dashed-kind edge from the used one to its user, named by the parameter", () => {
    const { edges } = buildGraph(flow, new Set([1, 2]));
    const refs = edges.filter((e) => e.kind === "ref");
    expect(refs.map((e) => [e.source, e.target, e.label])).toEqual([
      ["c:adc", "c:acq", "adc"],
      ["c:timebase", "c:acq", "horizontal"],
      ["c:acq", "c:meas", "record"],
    ]);
    expect(edges.filter((e) => e.kind === "wire").every((e) => !e.label)).toBe(
      true,
    );
  });

  it("puts the stages before the acquisition and the readers after it", () => {
    const { nodes } = buildGraph(flow, new Set([1, 2]));
    const x = Object.fromEntries(nodes.map((n) => [n.id, n.x]));
    expect(x["c:adc"]).toBeLessThan(x["c:acq"]);
    expect(x["c:timebase"]).toBeLessThan(x["c:acq"]);
    expect(x["c:acq"]).toBeLessThan(x["c:meas"]);
  });

  it("parks what only commands drive under a label of its own, below the channels", () => {
    const { nodes } = buildGraph(flow, new Set([1, 2]));
    const label = nodes.find((n) => n.kind === "label");
    const sys = nodes.find((n) => n.id === "c:sys");
    expect(label).toBeDefined();
    expect(sys!.y).toBeGreaterThanOrEqual(
      Math.max(
        ...nodes
          .filter((n) => n.id !== "c:sys" && n.kind !== "label")
          .map((n) => n.y),
      ),
    );
    expect(sys!.x).toBeGreaterThan(label!.x);
    // components with a wire or a dependency are not in that band
    expect(nodes.find((n) => n.id === "c:adc")!.y).toBeLessThan(sys!.y);
  });

  it("shows no command side when everything is connected", () => {
    expect(
      buildGraph(scope, new Set([1, 2])).nodes.some((n) => n.kind === "label"),
    ).toBe(false);
  });

  it("draws a dependency to a collapsed channel at its group", () => {
    const withChannelRef: InstrumentInternals = {
      ...flow,
      refs: [...flow.refs, { from: "ch2.front", to: "acq", param: "vertical" }],
    };
    const { edges } = buildGraph(withChannelRef, new Set([1]));
    expect(edges.map((e) => e.id)).toContain("g:2/out->c:acq/ref-in:vertical");
  });
});

// The SDS2104X Plus of examples/benches/siglent_scope_rc, captured from the engine
// (scripts/capture_internals_fixture.py): the signal-flow layout of a real model (#953).
const sds = fixture.internals as unknown as InstrumentInternals;
const columns = (expanded: ReadonlySet<number>) => {
  const { nodes } = buildGraph(sds, expanded);
  const byX = new Map<number, string[]>();
  for (const n of nodes) if (n.kind !== "label" && !n.id.endsWith(".cal")) byX.set(n.x, [...(byX.get(n.x) ?? []), n.id]);
  return [...byX].sort((a, b) => a[0] - b[0]).map(([, ids]) => ids.sort());
};

describe("buildGraph on a real model (#953)", () => {
  it("orders the columns by the signal: panel input, channel chain, ADC, decimator, acquisition, readers", () => {
    expect(columns(new Set([1, 2, 3, 4]))).toEqual([
      ["p:CH1", "p:CH2", "p:CH3", "p:CH4"],
      ["c:ch1.inp", "c:ch2.inp", "c:ch3.inp", "c:ch4.inp"],
      ["c:ch1.front", "c:ch2.front", "c:ch3.front", "c:ch4.front"],
      ["c:ch1.probe", "c:ch2.probe", "c:ch3.probe", "c:ch4.probe"],
      // what a block uses stands one column before it: the time base before the decimator
      ["c:adc", "c:timebase"],
      ["c:dec", "c:trigger"],
      ["c:acq"],
      ["c:meas", "c:xfer"],
    ]);
  });

  it("gives the same columns on every call (the layout is a pure function of the view)", () => {
    expect(columns(new Set([1]))).toEqual(columns(new Set([1])));
    expect(columns(new Set([1]))[1]).toEqual(["c:ch1.inp", "g:2", "g:3", "g:4"]);
  });

  it("puts the shared blocks where the channel rows meet, not above them", () => {
    const { nodes } = buildGraph(sds, new Set([1, 2, 3, 4]));
    const y = (id: string) => nodes.find((n) => n.id === id)!;
    const rows = ["c:ch1.probe", "c:ch4.probe"].map(y);
    const adc = y("c:adc");
    expect(adc.y).toBeGreaterThan(rows[0].y);
    expect(adc.y + adc.height).toBeLessThan(rows[1].y + rows[1].height);
  });

  it("keeps what a wire carries on its edge: analog, or a sample stream with rate and format", () => {
    const { edges } = buildGraph(sds, new Set([1, 2, 3, 4]));
    const flow = (id: string) => edges.find((e) => e.id === id)!.flow;
    expect(flow("c:ch1.front/out->c:ch1.probe/in")).toEqual({ medium: "signal", sample_rate_hz: null, format: null });
    const adc = flow("c:adc/out1->c:dec/in1")!;
    expect(adc).toMatchObject({ medium: "samples", format: { kind: "volts", bits: 8 } });
    expect(adc.sample_rate_hz! / 2e9).toBeCloseTo(1, 12);
    expect(flow("c:dec/out1->c:acq/in1")!.sample_rate_hz! / 1e6).toBeCloseTo(1, 12);
    expect(edges.filter((e) => e.kind === "ref").every((e) => e.flow === undefined)).toBe(true);
  });

  it("marks the outputs a probe opens on: signal and sample-stream outputs, not inputs", () => {
    const { nodes } = buildGraph(sds, new Set([1]));
    const rows = (id: string) => (nodes.find((n) => n.id === id) as Extract<(typeof nodes)[number], { kind: "component" }>).rows;
    expect(rows("c:adc").filter((r) => r.probe).map((r) => r.name)).toEqual(["out1", "out2", "out3", "out4"]);
    expect(rows("c:adc").filter((r) => r.side === "left").every((r) => !r.probe)).toBe(true);
    expect(rows("c:ch1.front").find((r) => r.name === "out")!.probe).toBe(true);
  });
});
