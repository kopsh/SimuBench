import { describe, expect, it } from "vitest";
import { filterBenches, groupInstruments } from "./model";

const benches = [
  { name: "awg_rc_scope", path: "benches/awg_rc_scope.bench.yaml", description: "RC low-pass" },
  { name: "lockin_rc", path: "benches/lockin_rc.bench.yaml", description: null },
  { name: "smu", path: "benches/smu.bench.yaml", description: "diode sweep" },
];

describe("filterBenches", () => {
  it("matches name, path or description, case-insensitively", () => {
    expect(filterBenches(benches, "LOCKIN").map((b) => b.name)).toEqual(["lockin_rc"]);
    expect(filterBenches(benches, "diode").map((b) => b.name)).toEqual(["smu"]);
    expect(filterBenches(benches, "  ")).toHaveLength(3);
  });

  it("puts the open bench first", () => {
    expect(filterBenches(benches, "", "benches/smu.bench.yaml").map((b) => b.name)).toEqual(["smu", "awg_rc_scope", "lockin_rc"]);
  });
});

const instruments = [
  { id: "b", manufacturer: "Siglent", model: "SDS2104X", instrument_class: "oscilloscope" },
  { id: "c", manufacturer: "Keysight", model: "34465A", instrument_class: "multimeter" },
  { id: "a", manufacturer: "Keysight", model: "DSOX1204G", instrument_class: "oscilloscope" },
  { id: "d", manufacturer: "Acme", model: "X1", instrument_class: "mystery" },
];

describe("groupInstruments", () => {
  it("groups by class in the given order, unknown classes last, sorted inside", () => {
    const groups = groupInstruments(instruments, "", ["oscilloscope", "multimeter"]);
    expect(groups.map((g) => g.cls)).toEqual(["oscilloscope", "multimeter", "mystery"]);
    expect(groups[0]!.items.map((i) => i.model)).toEqual(["DSOX1204G", "SDS2104X"]);
  });

  it("filters on manufacturer + model and drops empty groups", () => {
    const groups = groupInstruments(instruments, "keysight 344", ["oscilloscope", "multimeter"]);
    expect(groups).toEqual([{ cls: "multimeter", items: [instruments[1]] }]);
  });
});
