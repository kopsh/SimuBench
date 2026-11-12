import type { Meta, StoryObj } from "@storybook/react-vite";
import { ApiError, type InstrumentInfo, type InstrumentInternals } from "@/api/client";
import { ComponentDetails, InternalsPanel } from "./InternalsView";
import { InternalProbeView, type ProbeState } from "./ProbeView";
import { useState } from "react";
import { type InternalProbe, type MiniSignal, type Tune, CENTER, tuneParam } from "./probe";
import fixture from "./internals.fixture.json";
import specan from "./specan.fixture.json";

/**
 * The SDS2104X Plus built from components (issue 20), from a real session of
 * examples/benches/siglent_scope_rc with the generator on (scripts/capture_internals_fixture.py):
 * the internals, and the internal probe's answers at three ports (signal-chain.md 1.3, #953).
 */
const view = fixture.internals as unknown as InstrumentInternals;
const inst = fixture.instrument as unknown as InstrumentInfo;
const probes = fixture.probes as unknown as Record<string, InternalProbe>;
/** The thumbnail on every output (the generator feeds CH1; the other channels read 0). */
const signals = new Map(Object.entries(fixture.signals as unknown as Record<string, MiniSignal>));

/** A probe's state for a story: the captured answer, or one of the states without a signal. */
type Shown = "ready" | "stale" | "loading" | "withheld" | "error";
const stateOf = (port: string, shown: Shown): ProbeState =>
  shown === "loading"
    ? { kind: "loading" }
    : shown === "withheld"
      ? { kind: "withheld" }
      : shown === "error"
        ? { kind: "error", error: new ApiError(404, `no output port '${port}' inside scope`) }
        : { kind: "ready", probe: probes[port], stale: shown === "stale" };

function Internals({ selected, query, probe, shown = "ready" }: { selected?: string; query?: string; probe?: string; shown?: Shown }) {
  return (
    <div className="h-[560px] w-[1100px] rounded-md border">
      <InternalsPanel
        view={view}
        inst={inst}
        initial={{ selected, query, probe }}
        signals={signals}
        renderProbe={(slot) => (
          <InternalProbeView
            port={slot.port}
            heading={slot.heading}
            tag={slot.tag}
            state={stateOf(slot.port, shown)}
            onClose={slot.close}
          />
        )}
      />
    </div>
  );
}

const meta: Meta<typeof Internals> = {
  title: "Workbench/Instrument internals",
  component: Internals,
};
export default meta;
type Story = StoryObj<typeof Internals>;

/** Laid out along the signal (signal-chain.md 1.2): CH1's row into the ADC; analog wires thin, sample streams double, each labelled. */
export const Oscilloscope: Story = {};

/** The ADC selected (#739): its chain is lit — the channels that feed it, the decimator and acquisition, the readers after. */
export const AdcTraced: Story = { args: { selected: "adc" } };

/** A channel selected: its own chain, and the trigger that picks its channel by a setting (dotted). */
export const ChannelTraced: Story = { args: { selected: "ch1.front" } };

/** Finding where a command lands (#739): the transfer answers :WAVeform:DATA?. */
export const FindCommand: Story = { args: { query: ":WAV:DATA?" } };

/** A search that matches nothing explains what to type. */
export const FindNothing: Story = { args: { query: ":FOO:BAR" } };

/** The internal probe on the ADC's output: samples as steps, 2 GSa/s · 8-bit, how much of full scale it uses, the frame's FFT. */
export const ProbeAdc: Story = { args: { probe: "adc.out1" } };

/** The internal probe on an analog output: the analytic waveform and its lines. */
export const ProbeAnalog: Story = { args: { probe: "ch1.front.out" } };

/** A port that reads 0: why, traced upstream (CH2 is not wired). */
export const ProbeSilent: Story = { args: { probe: "ch2.front.out" } };

/**
 * The digital-IF spectrum analyzer (sb-sa-fft-1) with two tones 1 MHz apart on its input, from a
 * real session of web/e2e/fixtures/benches/sa-two-tone (signal-chain.md 1.5, #959): RF input →
 * attenuator → mixer → IF filter → ADC → down-converter → RBW filter / FFT → detector → video
 * filter → trace, the oscillator and its reference under the mixer.
 */
const sa = {
  view: specan.internals as unknown as InstrumentInternals,
  inst: specan.instrument as unknown as InstrumentInfo,
  probes: specan.probes as unknown as Record<string, InternalProbe>,
  signals: new Map(Object.entries(specan.signals as unknown as Record<string, MiniSignal>)),
};

function Analyzer({ probe, tune: start = CENTER }: { probe?: string; tune?: Tune }) {
  // The story answers the two tunings the fixture holds: the center, and the marker at 1 GHz.
  const [tune, setTune] = useState<Tune>(start);
  const answer = (port: string): ProbeState => {
    const hz = tuneParam(tune, sa.view.tuning, port);
    const found = sa.probes[hz == null ? port : `${port}@${hz.toExponential().replace("+", "")}`] ?? sa.probes[port];
    return { kind: "ready", probe: hz == null ? found : { ...found, tune_hz: hz } };
  };
  return (
    <div className="h-[760px] w-[1280px] rounded-md border" data-testid="analyzer-story">
      <InternalsPanel
        view={sa.view}
        inst={sa.inst}
        initial={{ probe, tune: start }}
        signals={sa.signals}
        renderProbe={(slot) => (
          <InternalProbeView
            port={slot.port}
            heading={slot.heading}
            tag={slot.tag}
            state={answer(slot.port)}
            tuned={slot.tuned ? { ...slot.tuned, tune, onTune: setTune } : undefined}
            onClose={slot.close}
          />
        )}
      />
    </div>
  );
}

/** The analyzer's chain along the signal, and the mixer's IF output: the two tones at 24.5 and 25.5 MHz with their third-order products, read at the center frequency ("tuned to"). */
export const SpectrumAnalyzer: StoryObj<typeof Analyzer> = {
  render: (args) => <Analyzer {...args} />,
  args: { probe: "mixer.out" },
};

/** The down-converter's IQ stream tuned to the marker (1 GHz): I and Q on one scale, and the two-sided spectrum on radio frequencies — a click on it retunes. */
export const SpectrumAnalyzerIq: StoryObj<typeof Analyzer> = {
  render: (args) => <Analyzer {...args} />,
  args: { probe: "ddc.out", tune: { kind: "mark", mark: "marker", index: 1 } },
};

/** The transform's output: frequency bins drawn as they are (no second FFT), with the noise floor. */
export const SpectrumAnalyzerBins: StoryObj<typeof Analyzer> = {
  render: (args) => <Analyzer {...args} />,
  args: { probe: "rbw.out" },
};

/** A component's details (#739): what controls the ADC, and the readings it affects, grouped by the component that answers them. */
function AdcDetails() {
  const comp = view.components.find((c) => c.type === "scope_adc")!;
  return (
    <div className="bg-card w-[320px] rounded-md border">
      <ComponentDetails comp={comp} inst={inst} view={view} />
    </div>
  );
}

export const ComponentDrivers: StoryObj<typeof AdcDetails> = {
  render: () => <AdcDetails />,
};

/** The probe panel's states without a signal: loading, validation mode (403), an error, and disconnected (the last signal kept). */
function ProbeStates() {
  const states: Shown[] = ["loading", "withheld", "error", "stale"];
  return (
    <div className="flex items-start gap-3">
      {states.map((shown) => (
        <div key={shown} className="bg-sidebar w-[320px] rounded-md border">
          <InternalProbeView
            port="adc.out1"
            heading="ADC 组 · out1"
            tag="数字 · 2 GSa/s · 8 位"
            state={stateOf("adc.out1", shown)}
            onClose={() => {}}
          />
        </div>
      ))}
    </div>
  );
}

export const ProbePanelStates: StoryObj<typeof ProbeStates> = {
  render: () => <ProbeStates />,
};
