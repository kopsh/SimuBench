import { describe, expect, it } from "vitest";
import type { SessionInfo } from "@/api/client";
import { driversJoined } from "./connect";

const port = (name: string, net: string | null, drives = false, direction = drives ? "out" : "in") => ({
  name,
  direction,
  medium: "electrical",
  net,
  channel: null,
  drives,
});
// The hello bench, cut down: the generator's CH1 drives the scope's CH1; CH2 drives the analyzer.
const hello = {
  instruments: [
    { name: "awg", ports: [port("CH1", "n1", true), port("CH2", "n2", true)] },
    { name: "scope", ports: [port("CH1", "n1"), port("CH2", null), port("CAL_OUT", null, true)] },
    { name: "sa", ports: [port("RF_IN", "n2")] },
  ],
  parts: [{ name: "rc", ports: [port("IN", null), port("OUT", null, false, "out")] }],
} as unknown as SessionInfo;

describe("driversJoined (#1269)", () => {
  it("names the two outputs when an output meets another output's net", () => {
    expect(driversJoined(hello, "scope.CAL_OUT", "awg.CH1")).toEqual(["awg.CH1", "scope.CAL_OUT"]);
    // through the nets: the scope's free CH2 is fine, but joining CH1's net to CH2's driver is not
    expect(driversJoined(hello, "scope.CH1", "awg.CH2")).toEqual(["awg.CH1", "awg.CH2"]);
  });
  it("lets an output reach an input, and a passive part's output (not a source)", () => {
    expect(driversJoined(hello, "scope.CAL_OUT", "scope.CH2")).toEqual([]);
    expect(driversJoined(hello, "awg.CH1", "rc.OUT")).toEqual([]);
  });
});
