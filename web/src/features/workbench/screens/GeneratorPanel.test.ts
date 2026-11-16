import { describe, expect, it } from "vitest";
import { outputShape } from "./GeneratorPanel";

describe("outputShape (#1252)", () => {
  it("draws a square high for the duty part of the period", () => {
    expect(outputShape("SQUARE", 0.2, 25)).toBe(1);
    expect(outputShape("SQU", 0.3, 25)).toBe(-1);
  });
  it("draws a sine through zero at the period start and its peak a quarter in", () => {
    expect(outputShape("SINE", 0)).toBeCloseTo(0);
    expect(outputShape("SIN", 0.25)).toBeCloseTo(1);
  });
  it("draws a ramp whose symmetry places its peak", () => {
    expect(outputShape("RAMP", 0.5, 50, 50)).toBeCloseTo(1);
    expect(outputShape("RAMP", 0.25, 50, 50)).toBeCloseTo(0);
    expect(outputShape("RAMP", 0.5, 50, 100)).toBeCloseTo(0);
  });
  it("repeats every period", () => {
    expect(outputShape("SINE", 1.25)).toBeCloseTo(outputShape("SINE", 0.25)!);
  });
  it("draws DC flat and gives up on shapes it cannot know", () => {
    expect(outputShape("DC", 0.4)).toBe(0);
    expect(outputShape("ARB", 0.4)).toBeNull();
    expect(outputShape("NOISE", 0.4)).toBeNull();
  });
});
