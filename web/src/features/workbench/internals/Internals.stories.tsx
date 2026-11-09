import type { Meta, StoryObj } from "@storybook/react-vite";
import type { InstrumentInfo, InstrumentInternals } from "@/api/client";
import { ComponentDetails, InternalsPanel } from "./InternalsView";
import fixture from "./internals.fixture.json";

/** The SDS2104X Plus built from components (issue 20), from a real session of examples/benches/siglent_scope_rc. */
function Internals({ selected, query }: { selected?: string; query?: string }) {
  return (
    <div className="h-[560px] w-[1100px] rounded-md border">
      <InternalsPanel
        view={fixture.internals as unknown as InstrumentInternals}
        inst={fixture.instrument as unknown as InstrumentInfo}
        initial={{ selected, query }}
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

export const Oscilloscope: Story = {};

/** The ADC selected (#739): its chain is lit — the acquisition it feeds and the readers after it. */
export const AdcTraced: Story = { args: { selected: "adc" } };

/** A channel selected: its own chain, and the trigger that picks its channel by a setting (dotted). */
export const ChannelTraced: Story = { args: { selected: "ch1.front" } };

/** Finding where a command lands (#739): the transfer answers :WAVeform:DATA?. */
export const FindCommand: Story = { args: { query: ":WAV:DATA?" } };

/** A search that matches nothing explains what to type. */
export const FindNothing: Story = { args: { query: ":FOO:BAR" } };

/** A component's details (#739): what controls the ADC, and the readings it affects, grouped by the component that answers them. */
function AdcDetails() {
  const view = fixture.internals as unknown as InstrumentInternals;
  const comp = view.components.find((c) => c.type === "scope_adc")!;
  return (
    <div className="bg-card w-[320px] rounded-md border">
      <ComponentDetails
        comp={comp}
        inst={fixture.instrument as unknown as InstrumentInfo}
        view={view}
      />
    </div>
  );
}

export const ComponentDrivers: StoryObj<typeof AdcDetails> = {
  render: () => <AdcDetails />,
};
