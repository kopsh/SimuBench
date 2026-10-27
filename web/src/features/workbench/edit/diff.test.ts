import { describe, expect, it } from "vitest";
import { diffItems } from "./diff";

const base = {
  schema: "simubench.bench/1",
  name: "b",
  instruments: { awg: { model: "sb-awg-2", state: { "ch1.frequency": 1000 } }, scope: { model: "sb-dso-4" } },
  duts: { rc: { type: "rc_lowpass", params: { r: 1000, c: 1e-7 } } },
  wires: [{ from: "awg.OUT1", to: "rc.IN" }],
  layout: { awg: { x: 0, y: 0 } },
};

describe("diffItems", () => {
  it("lists added, removed and changed items with their fields", () => {
    const after = {
      ...base,
      instruments: { awg: { model: "sb-awg-2", state: { "ch1.frequency": 2000 } }, dmm: { model: "sb-dmm-1" } },
      duts: { rc: { type: "rc_lowpass", params: { r: 2200, c: 1e-7 } } },
      wires: [{ from: "awg.OUT1", to: "rc.IN", accessories: [{ type: "coax_cable" }] }, { from: "rc.OUT", to: "dmm.IN" }],
      layout: { awg: { x: 10, y: 0 } },
    };
    const items = diffItems(base, after);
    const brief = items.map((i) => `${i.section}:${i.name}:${i.kind}`);
    expect(brief).toEqual([
      "instruments:awg:changed",
      "instruments:scope:removed",
      "instruments:dmm:added",
      "duts:rc:changed",
      "wires:awg.OUT1 → rc.IN:changed",
      "wires:rc.OUT → dmm.IN:added",
      "layout:awg:changed",
    ]);
    expect(items[0].changes).toEqual([{ path: "state.ch1.frequency", before: 1000, after: 2000 }]);
    expect(items[3].changes).toEqual([{ path: "params.r", before: 1000, after: 2200 }]);
  });

  it("finds nothing between equal versions", () => {
    expect(diffItems(base, structuredClone(base))).toEqual([]);
  });
});
