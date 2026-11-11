import type { Meta, StoryObj } from "@storybook/react-vite";
import { ApiError, type InstrumentInfo, type InstrumentInternals } from "@/api/client";
import { ComponentDetails, InternalsPanel } from "./InternalsView";
import { InternalProbeView, type ProbeState } from "./ProbeView";
import type { InternalProbe } from "./probe";
import fixture from "./internals.fixture.json";

/**
 * The SDS2104X Plus built from components (issue 20), from a real session of
 * examples/benches/siglent_scope_rc with the generator on (scripts/capture_internals_fixture.py):
 * the internals, and the internal probe's answers at three ports (signal-chain.md 1.3, #953).
 */
const view = fixture.internals as unknown as InstrumentInternals;
const inst = fixture.instrument as unknown as InstrumentInfo;
const probes = fixture.probes as unknown as Record<string, InternalProbe>;

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
