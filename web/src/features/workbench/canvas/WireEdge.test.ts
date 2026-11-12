import { Position } from "@xyflow/react";
import { describe, expect, it } from "vitest";
import { acRms, aroundPath, readoutOf, sparkline } from "./WireEdge";

describe("wire routing (#277)", () => {
  it("keeps the curve when the pins face each other", () => {
    const s = { x: 100, y: 50, side: Position.Right };
    const t = { x: 300, y: 80, side: Position.Left };
    expect(aroundPath(s, t, [{ top: 0, left: 0, right: 100 }, { top: 40, left: 300, right: 400 }])).toBeNull();
    // Nodes close together (less than two stubs apart) still face each other.
    expect(aroundPath(s, { x: 150, y: 80, side: Position.Left }, [{ top: 0, left: 0, right: 100 }, { top: 40, left: 300, right: 400 }])).toBeNull();
  });

  it("runs above both nodes when the pins face away from each other", () => {
    // An output on the right of a node placed right of its load's input on the left.
    const s = { x: 700, y: 60, side: Position.Right };
    const t = { x: 100, y: 50, side: Position.Left };
    const found = aroundPath(s, t, [
      { top: 30, left: 450, right: 700 },
      { top: 20, left: 100, right: 300 },
      { top: 5, left: 900, right: 1000 }, // off to the side: not crossed, does not lift the run
    ]);
    expect(found).not.toBeNull();
    const [path, , labelY] = found!;
    expect(labelY).toBe(20 - 40); // just above the higher node it crosses
    expect(path.startsWith("M 700 60 L 740 60")).toBe(true); // straight out of the pin first
    expect(path.endsWith("L 100 50")).toBe(true);
  });

  it("clears a third node lying under the run", () => {
    const s = { x: 300, y: 100, side: Position.Left };
    const t = { x: 800, y: 100, side: Position.Left };
    const [, , labelY] = aroundPath(s, t, [{ top: 80, left: 300, right: 500 }, { top: 10, left: 400, right: 600 }, { top: 80, left: 800, right: 1000 }])!;
    expect(labelY).toBe(10 - 40);
  });
});

describe("a live wire's readout", () => {
  it("gives a steady level as DC and anything that moves as RMS", () => {
    expect(readoutOf({ dc: 5, rms: 5 })).toEqual({ kind: "dc", value: 5 });
    expect(readoutOf({ dc: 0, rms: 0.707 })).toEqual({ kind: "ac", value: 0.707 });
    // A sine on an offset: the figure is the whole RMS, and the alternating part is what is left without the DC.
    expect(readoutOf({ dc: 3, rms: 5 }).kind).toBe("ac");
    expect(acRms({ dc: 3, rms: 5 })).toBeCloseTo(4, 12);
  });

  it("draws the thumbnail over its own range, and a flat one through the middle", () => {
    expect(sparkline([0, 1, 0], 20, 10, 0)).toBe("0.0,10.0 10.0,0.0 20.0,10.0");
    expect(sparkline([2, 2], 20, 10)).toBe("0.0,5.0 20.0,5.0");
    expect(sparkline([], 20, 10)).toBe("");
  });
});
