import type { Meta, StoryObj } from "@storybook/react-vite";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { InstrumentInfo } from "@/api/client";
import fixture from "./panel.fixture.json";
import { PanelScreen } from "./PanelScreen";

/**
 * Front panels from the class default layout (issue 128): the 34465A of examples/benches/keysight-34465a
 * and the 5522A of examples/benches/fluke-5522a at 10 V in operate, from real sessions.
 */
function Panel({ which }: { which: "multimeter" | "calibrator" }) {
  const entry = fixture[which];
  return (
    <QueryClientProvider client={new QueryClient()}>
      <div className="h-[560px] w-[940px] rounded-md border">
        <PanelScreen sid="" inst={entry.instrument as unknown as InstrumentInfo} readonly={false} values={entry.readings} />
      </div>
    </QueryClientProvider>
  );
}

const meta: Meta<typeof Panel> = { title: "Instruments/Front panel", component: Panel };
export default meta;
type Story = StoryObj<typeof Panel>;

export const Multimeter: Story = { args: { which: "multimeter" } };
export const Calibrator: Story = { args: { which: "calibrator" } };
