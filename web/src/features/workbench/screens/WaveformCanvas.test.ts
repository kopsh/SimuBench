import { expect, it } from "vitest";
import type { Trace } from "@/api/client";
import { axisLabels, sameTraces, traceSpan } from "./WaveformCanvas";

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

it("labels every vertical division around a non-zero channel offset", () => {
  const labels = axisLabels({ scale: 0.25, offset: 0.5 });
  expect(labels.volts).toEqual([
    "1.5 V",
    "1.25 V",
    "1 V",
    "750 mV",
    "500 mV",
    "250 mV",
    "0 V",
    "−250 mV",
  ]);
});

it.each([
  {
    reference: "left",
    span: { left: 2e-6, width: 10e-6 },
    expected: ["2 µs", "4 µs", "6 µs", "8 µs", "10 µs", "12 µs"],
  },
  {
    reference: "centre",
    span: { left: -3e-6, width: 10e-6 },
    expected: ["−3 µs", "−1 µs", "1 µs", "3 µs", "5 µs", "7 µs"],
  },
  {
    reference: "right",
    span: { left: -8e-6, width: 10e-6 },
    expected: ["−8 µs", "−6 µs", "−4 µs", "−2 µs", "0 s", "2 µs"],
  },
])("labels the $reference-referenced time axis after horizontal positioning", ({ span, expected }) => {
  expect(axisLabels(undefined, span).times).toEqual(expected);
});

it("treats a fresh array of the same traces as unchanged, so it is not redrawn", () => {
  const a = { channel: 1 } as Trace;
  const b = { channel: 2 } as Trace;
  expect(sameTraces([a, b], [a, b])).toBe(true);
  expect(sameTraces([a, b], [b, a])).toBe(false);
  expect(sameTraces([a], [a, b])).toBe(false);
  expect(sameTraces([a], [{ ...a }])).toBe(false);
});
