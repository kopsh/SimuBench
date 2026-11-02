import type { Meta, StoryObj } from "@storybook/react-vite";
import type { NetLevel, SessionInfo } from "@/api/client";
import { Canvas } from "./Canvas";
import benches from "./benches.fixture.json";
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

/**
 * The default view of three example benches (#277, #314): the counter (OUT1 → CH1 faces away),
 * the B2901A with its DUT, the DSO-X 2024A behind an RC. Wires must not cross port labels or run
 * under a node. Sessions from examples/ and validation/ at power-on, command clock.
 */
function BenchCanvas({ bench }: { bench: keyof typeof benches }) {
  const entry = benches[bench];
  const levels = entry.levels.nets as NetLevel[];
  return (
    <div className="h-[420px] w-[960px] rounded-md border">
      <Canvas view={entry.session as unknown as SessionInfo} levels={new Map(levels.map((n) => [n.net, n]))} />
    </div>
  );
}

export const Counter: Story = { render: () => <BenchCanvas bench="counter" /> };
export const B2901a: Story = { render: () => <BenchCanvas bench="b2901a" /> };
export const Dsox2024a: Story = { render: () => <BenchCanvas bench="dsox2024a" /> };
