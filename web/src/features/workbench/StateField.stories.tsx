import type { Meta, StoryObj } from "@storybook/react-vite";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { StateVarInfo } from "@/api/client";
import { StateField } from "./StateField";

/**
 * The number field's stepper (#738, #742): a plain value, a ladder, the greyed arrow at the end
 * of the range, and the states where stepping is unavailable — covered by the automatic switch,
 * or a read-only setting. The arrows show on hover or focus on the real panel; the story keeps
 * them up so the visual baseline sees them.
 */
const v = (path: string, rest: Record<string, unknown>) =>
  ({ path, channel: null, readonly: false, dangerous: false, ...rest }) as unknown as StateVarInfo;

/** The arrows ignore their hover/focus hiding inside the story: the baseline is about them. */
function Stepper({ children }: { children: React.ReactNode }) {
  return (
    <div className="group/num w-72 p-2">
      <style>{".group\\/num .opacity-0 { opacity: 1; }"}</style>
      {children}
    </div>
  );
}

function Field({ v, inactive }: { v: StateVarInfo; inactive?: boolean }) {
  return (
    <QueryClientProvider client={new QueryClient()}>
      <Stepper>
        <StateField sid="story" instrument="dmm" v={v} inactive={inactive} onCommit={() => {}} />
      </Stepper>
    </QueryClientProvider>
  );
}

const meta: Meta<typeof Field> = { title: "Workbench/StateField", component: Field };
export default meta;

type Story = StoryObj<typeof Field>;

export const Plain: Story = {
  name: "普通",
  render: () => (
    <Field
      v={v("sense.offset", { type: "float", unit: "V", min: -5, max: 5, value: 1.5, label: "偏移", label_en: "Offset" })}
    />
  ),
};

export const Ladder: Story = {
  name: "带梯度",
  render: () => (
    <Field
      v={v("ch1.scale", { type: "float", unit: "V", min: 0.001, max: 10, value: 0.05, ladder: "1-2-5", label: "垂直档位", label_en: "Scale" })}
    />
  ),
};

export const AtBoundary: Story = {
  name: "到边界按钮置灰",
  render: () => (
    <Field
      v={v("timebase.scale", { type: "float", unit: "S", min: 2e-9, max: 50, value: 50, label: "时基", label_en: "Timebase" })}
    />
  ),
};

export const AutoCovered: Story = {
  name: "自动覆盖",
  render: () => (
    <Field
      inactive
      v={v("sense.range", { type: "float", unit: "V", min: 0, max: 100, value: 10, label: "量程", label_en: "Range" })}
    />
  ),
};

export const Readonly: Story = {
  name: "只读",
  render: () => (
    <Field
      v={v("sense.cal_due", { type: "float", unit: "V", readonly: true, value: 0, label: "校准常数", label_en: "Calibration constant" })}
    />
  ),
};
