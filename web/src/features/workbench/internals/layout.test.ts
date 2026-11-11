import { describe, expect, it } from "vitest";
import type { InstrumentInternals } from "@/api/client";
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
