import { describe, expect, it } from "vitest";
import { pushHistory, sweepDurationMs, sweepProgress } from "./motion";

describe("pushHistory", () => {
  it("keeps the newest acquisitions first, up to the limit", () => {
    expect(pushHistory(["b", "c", "d"], "a", 3)).toEqual(["a", "b", "c"]);
    expect(pushHistory([], undefined, 3)).toEqual([]);
  });
});

describe("sweep timing", () => {
  it("follows the sweep time inside the visible range", () => {
    expect(sweepDurationMs(0.002)).toBe(250); // a 2 ms sweep is still seen
    expect(sweepDurationMs(0.6)).toBe(600);
    expect(sweepDurationMs(30)).toBe(1200); // never slower than the data
    expect(sweepDurationMs(Number.NaN)).toBe(250);
  });

  it("runs linearly from 0 to 1", () => {
    expect(sweepProgress(-10, 500)).toBe(0);
    expect(sweepProgress(250, 500)).toBe(0.5);
    expect(sweepProgress(900, 500)).toBe(1);
    expect(sweepProgress(10, 0)).toBe(1);
  });
});
