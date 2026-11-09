import type { Meta, StoryObj } from "@storybook/react-vite";
import type { InstrumentInfo, InstrumentInternals } from "@/api/client";
import { ComponentDetails, InternalsPanel } from "./InternalsView";
import fixture from "./internals.fixture.json";

/** The SDS2104X Plus built from components (issue 20), from a real session of examples/benches/siglent_scope_rc. */
function Internals() {
  return (
    <div className="h-[520px] w-[960px] rounded-md border">
      <InternalsPanel
        view={fixture.internals as unknown as InstrumentInternals}
        inst={fixture.instrument as unknown as InstrumentInfo}
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

/** A component's details with the settings and commands that drive it and the readings it affects (#739): the ADC. */
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
