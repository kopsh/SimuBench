import type { Meta, StoryObj } from "@storybook/react-vite";
import { WaveformCanvas } from "./WaveformCanvas";

const N = 1000;
const dt = 4e-6; // 4 ms on screen
const sine = (amp: number, f: number, phase = 0) => Array.from({ length: N }, (_, i) => amp * Math.sin(2 * Math.PI * f * (i - N / 2) * dt + phase));

/** The oscilloscope graticule: 10 × 8 divisions, channel colors, trigger marker. */
const meta: Meta<typeof WaveformCanvas> = {
  title: "Instruments/Waveform canvas",
  component: WaveformCanvas,
  render: (args) => (
    <div className="h-80 max-w-3xl">
      <WaveformCanvas {...args} className="h-full w-full" />
    </div>
  ),
};
export default meta;
type Story = StoryObj<typeof WaveformCanvas>;

export const TwoChannels: Story = {
  args: {
    traces: [
      { channel: 1, t0_s: (-N / 2) * dt, dt_s: dt, points: N, volts: sine(1, 1000), scale: 0.5, offset: 0, triggered: true, silence: [], minmax: null },
      { channel: 2, t0_s: (-N / 2) * dt, dt_s: dt, points: N, volts: sine(0.707, 1000, -Math.PI / 4), scale: 0.5, offset: 0, triggered: true, silence: [], minmax: null },
    ],
    triggerLevel: 0,
    triggerChannel: 1,
  },
};

export const NoSignal: Story = { args: { traces: [] } };

/** Non-zero offset and horizontal position keep both sets of axis labels in the visual baseline (#1341). */
export const OffsetAndPosition: Story = {
  args: {
    traces: [
      { channel: 1, t0_s: (-N / 2) * dt, dt_s: dt, points: N, volts: sine(0.5, 1000), scale: 0.25, offset: 0.5, triggered: true, silence: [], minmax: null },
    ],
    triggerLevel: 0.5,
    triggerChannel: 1,
    axisChannel: 1,
    window: { left: -1.5e-3, width: 4e-3 },
  },
};
