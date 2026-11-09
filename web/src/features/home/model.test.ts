import { describe, expect, it } from "vitest";
import { classCounts, filterBenches, filterInstruments } from "./model";

const benches = [
  { name: "hello", path: "hello.bench.yaml", description: "函数发生器 → 示波器 + 频谱仪" },
  { name: "lockin_rc", path: "benches/lockin_rc.bench.yaml", description: null },
  { name: "smu", path: "benches/smu.bench.yaml", description: "diode sweep" },
];

describe("filterBenches", () => {
  it("matches name, path or description, case-insensitively", () => {
    expect(filterBenches(benches, "LOCKIN").map((b) => b.name)).toEqual(["lockin_rc"]);
    expect(filterBenches(benches, "频谱").map((b) => b.name)).toEqual(["hello"]);
    expect(filterBenches(benches, "  ")).toHaveLength(3);
  });

  it("puts the open bench first", () => {
    expect(filterBenches(benches, "", "benches/smu.bench.yaml").map((b) => b.name)).toEqual(["smu", "hello", "lockin_rc"]);
  });
});

const instruments = [
  { id: "b", manufacturer: "Siglent", model: "SDS2104X Plus", instrument_class: "oscilloscope" },
  { id: "c", manufacturer: "Keysight", model: "34465A", instrument_class: "multimeter" },
  { id: "a", manufacturer: "Keysight", model: "DSOX2024A", instrument_class: "oscilloscope" },
  { id: "d", manufacturer: "Acme", model: "X1", instrument_class: "mystery" },
  { id: "my-c", manufacturer: "Keysight", model: "34465A", instrument_class: "multimeter", workspace: true },
];
const ORDER = ["oscilloscope", "multimeter"];

describe("classCounts", () => {
  it("counts each class present, in the given order, unknown classes last", () => {
    expect(classCounts(instruments, ORDER)).toEqual([
      { cls: "oscilloscope", count: 2 },
      { cls: "multimeter", count: 2 },
      { cls: "mystery", count: 1 },
    ]);
  });
});

describe("filterInstruments", () => {
  it("puts the user's own definitions first, then class order, maker and model", () => {
    expect(filterInstruments(instruments, "", null, ORDER).map((i) => i.id)).toEqual(["my-c", "a", "b", "c", "d"]);
  });

  it("filters by class and by manufacturer + model", () => {
    expect(filterInstruments(instruments, "", "oscilloscope", ORDER).map((i) => i.id)).toEqual(["a", "b"]);
    expect(filterInstruments(instruments, "keysight 344", null, ORDER).map((i) => i.id)).toEqual(["my-c", "c"]);
  });
});
