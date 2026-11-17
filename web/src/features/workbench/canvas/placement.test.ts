import { describe, expect, it } from "vitest";
import { freeSpot, intersects, type Rect } from "./placement";

const size = { width: 240, height: 140 };
// The hello bench as the canvas lays it out: the generator on the left, scope and analyzer stacked on the right.
const hello: Rect[] = [
  { x: 0, y: 120, width: 240, height: 150 },
  { x: 420, y: 0, width: 260, height: 170 },
  { x: 420, y: 240, width: 260, height: 170 },
];

describe("freeSpot (#1258)", () => {
  it("keeps the middle of the view when it is free", () => {
    expect(freeSpot(hello, size, { x: 800, y: 600 })).toEqual({ x: 800, y: 600 });
  });
  it("moves off the analyzer it would cover, to the nearest free cell", () => {
    const at = freeSpot(hello, size, { x: 294, y: 191 }); // where the add put the 34465A before
    const placed = { ...at, ...size };
    expect(hello.some((n) => intersects(placed, n, 40))).toBe(false);
    expect(Math.hypot(at.x - 294, at.y - 191)).toBeLessThan(600); // near the view's middle, not far away
  });
  it("places on an empty bench where asked", () => {
    expect(freeSpot([], size, { x: 10.4, y: -3.6 })).toEqual({ x: 10, y: -4 });
  });
});
