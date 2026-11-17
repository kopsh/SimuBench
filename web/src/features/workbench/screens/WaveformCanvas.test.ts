import { expect, it } from "vitest";
import { traceSpan } from "./WaveformCanvas";

// a 50 ns record of 100 samples, the trigger at its centre (#1333)
const rec = { t0_s: -25e-9, dt_s: 0.5e-9, volts: new Array<number>(100).fill(0) };

it("fills the width without a window", () => {
  const { xOf, i0, i1 } = traceSpan(rec, 1000);
  expect([i0, i1, xOf(0), xOf(99)]).toEqual([0, 99, 0, 1000]);
});

it("draws a stopped record at its real width after the timebase slowed down", () => {
  // 5 us screen (500 ns/div): the record is a hundredth of the width, around the centre
  const { xOf, i0, i1 } = traceSpan(rec, 1000, { left: -2.5e-6, width: 5e-6 });
  expect([i0, i1]).toEqual([0, 99]);
  expect(xOf(0)).toBeCloseTo(495, 6);
  expect(xOf(100)).toBeCloseTo(505, 6);
});

it("shows only the stretch of the record the faster timebase covers", () => {
  // 10 ns screen (1 ns/div): samples 30..70 are on screen
  const { xOf, i0, i1 } = traceSpan(rec, 1000, { left: -5e-9, width: 10e-9 });
  expect(i0).toBeGreaterThanOrEqual(38);
  expect(i1).toBeLessThanOrEqual(62);
  expect(xOf(50)).toBeCloseTo(500, 6);
});
