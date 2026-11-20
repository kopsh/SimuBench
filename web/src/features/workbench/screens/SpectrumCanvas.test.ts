import { describe, expect, it } from "vitest";
import { dbAt, dbLabels, dbY, markerPoint } from "./SpectrumCanvas";

describe("the spectrum dB scale", () => {
  it("stands the reference level on the top line and 10 dB per division under it", () => {
    expect(dbAt(0, 10)).toBe(10);
    expect(dbAt(1, 10)).toBe(0);
    expect(dbAt(5, 10)).toBe(-40);
    expect(dbAt(10, 10)).toBe(-90);
  });

  it("labels every other division, the top one with the unit (#1222)", () => {
    expect(dbLabels(10)).toEqual(["10 dBm", "-10", "-30", "-50", "-70", "-90"]);
    expect(dbLabels(-10)).toEqual(["-10 dBm", "-30", "-50", "-70", "-90", "-110"]);
  });

  it("puts the reference level on the top edge and one division per tenth of the height", () => {
    expect(dbY(10, 10, 400)).toBe(0);
    expect(dbY(0, 10, 400)).toBe(40);
    expect(dbY(-90, 10, 400)).toBe(400);
  });
});

describe("the marker landing point", () => {
  const span = { start: 1e6, stop: 3e6 };
  it("sits at its frequency across the width and its level down the height", () => {
    expect(markerPoint({ x: 2e6, y: -40 }, span, 400, 200, 10)).toEqual({ x: 200, y: 100 });
  });
  it("keeps a marker above the reference level a pixel inside the plot", () => {
    expect(markerPoint({ x: 2e6, y: 40 }, span, 400, 200, 10)?.y).toBe(1);
  });
  it("lands a marker outside the span off the canvas, unclamped", () => {
    expect(markerPoint({ x: 0, y: -40 }, span, 400, 200, 10)?.x).toBeLessThan(0);
  });
  it("is nowhere while the analyzer has no span", () => {
    expect(markerPoint({ x: 2e6, y: -40 }, { start: 0, stop: 0 }, 400, 200, 10)).toBeUndefined();
  });
});
