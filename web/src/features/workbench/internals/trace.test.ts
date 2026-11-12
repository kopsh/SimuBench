import { describe, expect, it } from "vitest";
import type { InstrumentInternals } from "@/api/client";
import fixture from "./internals.fixture.json";
import {
  commandForm,
  controlRows,
  link,
  matchesHeader,
  operationCommands,
  pathTrace,
  pickedChannel,
  readingGroups,
  search,
  traceOf,
} from "./trace";

// The SDS2104X Plus of examples/benches/siglent_scope_rc (#739).
const view = fixture.internals as unknown as InstrumentInternals;
const comp = (name: string) => view.components.find((c) => c.name === name)!;

describe("traceOf", () => {
  it("lights what feeds the ADC and what it reaches, down to the readers", () => {
    // Since the sample stream (D67) every channel's signal runs through the ADC, then the
    // decimator, to the acquisition; the ADC is also a dependency of the acquisition.
    const t = traceOf(view, "adc");
    const chain = [1, 2, 3, 4].flatMap((n) => [`ch${n}.inp`, `ch${n}.front`, `ch${n}.probe`, `p:CH${n}`]);
    expect([...t.nodes].sort()).toEqual(["acq", "adc", "dec", "meas", "xfer", ...chain].sort());
    expect(t.links).toEqual(
      new Set([
        link("adc", "dec"),
        link("dec", "acq"),
        link("adc", "acq"),
        link("acq", "meas"),
        link("acq", "xfer"),
        ...[1, 2, 3, 4].flatMap((n) => [
          link(`p:CH${n}`, `ch${n}.inp`),
          link(`ch${n}.inp`, `ch${n}.front`),
          link(`ch${n}.front`, `ch${n}.probe`),
          link(`ch${n}.probe`, "adc"),
        ]),
      ]),
    );
  });

  it("lights a channel's own chain from its panel input, not the other channels", () => {
    const t = traceOf(view, "ch1.front");
    for (const n of ["p:CH1", "ch1.inp", "ch1.probe", "acq", "xfer", "meas"])
      expect(t.nodes.has(n), n).toBe(true);
    expect(t.nodes.has("ch2.front")).toBe(false);
    // The trigger is reached only by its source setting: a link of its own, not through the acquisition.
    expect(t.links.has(link("ch1.front", "trigger"))).toBe(true);
    expect(t.links.has(link("ch1.front", "xfer"))).toBe(false);
  });

  it("lights a reader's sources, the channels included", () => {
    const t = traceOf(view, "xfer");
    expect(t.nodes.has("acq") && t.nodes.has("ch3.probe")).toBe(true);
    expect(t.nodes.has("meas")).toBe(false);
  });
});

describe("pathTrace", () => {
  it("lights one route only", () => {
    expect(pathTrace(["adc", "acq", "xfer"]).links).toEqual(
      new Set(["adc>acq", "acq>xfer"]),
    );
  });
});

describe("controlRows", () => {
  it("joins a setting with the command that writes it and the other blocks that follow it", () => {
    expect(controlRows(view, comp("adc"))).toEqual([
      {
        setting: "acquire.resolution",
        commands: [":ACQuire:RESolution[?]"],
        alsoDrives: ["xfer"],
      },
    ]);
  });

  it("names the channel's own command", () => {
    const row = controlRows(view, comp("ch2.front")).find(
      (r) => r.setting === "ch2.scale",
    )!;
    expect(row.commands).toEqual([":CHANnel2:SCALe[?]"]);
  });

  it("lists the commands that only run an operation apart", () => {
    const ops = operationCommands(comp("acq"));
    expect(ops).toContain(":TRIGger:RUN");
    expect(ops).toContain("*CAL?");
  });
});

describe("readingGroups", () => {
  it("puts the readers first and the acquisition's own queries last", () => {
    const groups = readingGroups(comp("adc"));
    expect(new Set(groups.slice(0, 2).map((g) => g.provider))).toEqual(
      new Set(["meas", "xfer"]),
    );
    expect(groups.map((g) => g.kind)).toEqual(["result", "result", "onTheWay"]);
    expect(groups[2].provider).toBe("acq");
    const xfer = groups.find((g) => g.provider === "xfer")!;
    expect(xfer.path).toEqual(["adc", "acq", "xfer"]);
    expect(xfer.headers).toContain(":WAVeform:DATA?");
  });

  it("carries the setting that must pick the channel", () => {
    const xfer = readingGroups(comp("ch1.front")).find(
      (g) => g.provider === "xfer",
    )!;
    expect(xfer.selectedBy).toBe("waveform.source");
  });

  it("keeps a reader's own queries as its own", () => {
    expect(readingGroups(comp("xfer"))[0].kind).toBe("self");
  });
});

describe("commandForm / pickedChannel", () => {
  it("writes how a command is used", () => {
    expect(commandForm({ header: ":A", set: true, query: false })).toBe(":A");
    expect(commandForm({ header: ":A", set: false, query: true })).toBe(":A?");
    expect(commandForm({ header: ":A" })).toBe(":A[?]");
  });

  it("reads the channel a source names", () => {
    expect(pickedChannel("C2")).toBe(2);
    expect(pickedChannel("CHANnel3")).toBe(3);
    expect(pickedChannel("MATH")).toBeNull();
  });
});

describe("matchesHeader", () => {
  it.each([
    [":WAVeform:DATA", ":WAV:DATA?"],
    [":WAVeform:DATA", "waveform:data"],
    [":CHANnel1:SCALe", ":CHAN1:SCAL?"],
    [":CHANnel<ch>:SCALe", "CHAN4:SCAL 0.1"],
    ["[:SOURce1]:FUNCtion:PULSe:PERiod", "FUNC:PULS:PER"],
    [":MEASure[:SCALar]:VOLTage[:DC]", "MEAS:VOLT?"],
    ["*CAL", "*cal?"],
  ])("%s accepts %s", (pattern, typed) => {
    expect(matchesHeader(pattern, typed)).toBe(true);
  });

  it.each([
    [":WAVeform:DATA", ":WAVE:DAT"],
    [":CHANnel1:SCALe", ":CHAN2:SCAL"],
    [":WAVeform:DATA", ":WAV"],
  ])("%s rejects %s", (pattern, typed) => {
    expect(matchesHeader(pattern, typed)).toBe(false);
  });
});

describe("search", () => {
  const label = () => undefined;
  it("finds the component a typed command lands on", () => {
    expect(search(view, ":WAV:DATA?", label)).toEqual([
      { component: "xfer", text: ":WAVeform:DATA?", kind: "command" },
    ]);
  });

  it("finds every block a setting drives", () => {
    const hits = search(view, "acquire.resolution", label);
    expect(new Set(hits.map((h) => h.component))).toEqual(new Set(["adc", "xfer"]));
  });

  it("finds a setting by its label", () => {
    const hits = search(view, "分辨率", (p) => (p === "acquire.resolution" ? "分辨率" : undefined));
    expect(hits.some((h) => h.component === "adc" && h.kind === "setting")).toBe(true);
  });

  it("finds nothing for an empty query", () => {
    expect(search(view, "  ", label)).toEqual([]);
  });
});
