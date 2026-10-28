import { describe, expect, it } from "vitest";
import type { InstrumentInternals } from "@/api/client";
import { buildGraph } from "./layout";

const port = (name: string, direction: string, medium = "signal", channel: number | null = null) => ({ name, direction, medium, channel, net: null });

// A two-channel scope: CH<n> -> inp -> front -> acq.in<n>.
const scope: InstrumentInternals = {
  instrument: "scope",
  definition_id: "sb-dso-2x",
  channels: 2,
  components: [
    { name: "acq", type: "scope_acquisition", channel: null, doc: null, params: [], ports: [port("in1", "in"), port("in2", "in")] },
    ...[1, 2].flatMap((n) => [
      { name: `ch${n}.inp`, type: "input_port", channel: n, doc: null, params: [], ports: [port("pin", "in", "electrical", n), port("out", "out", "signal", n)] },
      { name: `ch${n}.front`, type: "scope_frontend", channel: n, doc: null, params: [], ports: [port("in", "in", "signal", n), port("out", "out", "signal", n)] },
    ]),
  ],
  wires: [1, 2].flatMap((n) => [
    { from: `ch${n}.inp.out`, to: `ch${n}.front.in` },
    { from: `ch${n}.front.out`, to: `acq.in${n}` },
  ]),
  ports: [1, 2].map((n) => ({ name: `CH${n}`, target: `ch${n}.inp.pin`, direction: "in", medium: "electrical", channel: n })),
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

  it("does not let nodes in a column overlap", () => {
    const { nodes } = buildGraph(scope, new Set([1, 2]));
    const byColumn = new Map<number, typeof nodes>();
    for (const n of nodes) byColumn.set(n.x, [...(byColumn.get(n.x) ?? []), n]);
    for (const col of byColumn.values()) {
      const sorted = [...col].sort((a, b) => a.y - b.y);
      for (let i = 1; i < sorted.length; i++) expect(sorted[i].y).toBeGreaterThanOrEqual(sorted[i - 1].y + sorted[i - 1].height);
    }
  });
});
