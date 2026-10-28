import type { Meta, StoryObj } from "@storybook/react-vite";
import type { NetLevel, SessionInfo } from "@/api/client";
import { Canvas } from "./Canvas";
import fixture from "./canvas.fixture.json";

/**
 * The bench canvas with live levels (D44), from a real session of examples/benches/awg_rc_scope:
 * silent while the generator's output is off (grey wires, flat thumbnail), live once it is on
 * (wires flow in the channel color, the scope node shows the waveform).
 */
function LiveCanvas({ live }: { live: boolean }) {
  const view = (live ? fixture.session : fixture.silentSession) as unknown as SessionInfo;
  const levels = (live ? fixture.liveLevels : fixture.silentLevels).nets as NetLevel[];
  return (
    <div className="h-[420px] w-[960px] rounded-md border">
      <Canvas view={view} levels={new Map(levels.map((n) => [n.net, n]))} />
    </div>
  );
}

const meta: Meta<typeof LiveCanvas> = { title: "Workbench/Live canvas", component: LiveCanvas };
export default meta;
type Story = StoryObj<typeof LiveCanvas>;

export const OutputOff: Story = { args: { live: false } };
export const OutputOn: Story = { args: { live: true } };
