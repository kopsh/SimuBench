import type { Meta, StoryObj } from "@storybook/react-vite";
import type { InstrumentInfo, InstrumentInternals } from "@/api/client";
import { InternalsPanel } from "./InternalsView";
import fixture from "./internals.fixture.json";

/** The SDS2104X Plus built from components (issue 20), from a real session of examples/benches/siglent_scope_rc. */
function Internals() {
  return (
    <div className="h-[520px] w-[960px] rounded-md border">
      <InternalsPanel view={fixture.internals as unknown as InstrumentInternals} inst={fixture.instrument as unknown as InstrumentInfo} />
    </div>
  );
}

const meta: Meta<typeof Internals> = { title: "Workbench/Instrument internals", component: Internals };
export default meta;
type Story = StoryObj<typeof Internals>;

export const Oscilloscope: Story = {};
