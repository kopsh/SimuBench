import type { Meta, StoryObj } from "@storybook/react-vite";
import { AssistantView, type AssistantViewProps } from "./AssistantView";
import type { AssistantTurn } from "./model";

/**
 * The AI assistant panel (#877) in each state: not configured (the way to the settings), empty, a
 * turn streaming with its progress lines, a finished turn with what changed and the restore
 * button, a turn the server stopped for going in circles, and a stopped one.
 */
const base: AssistantTurn = {
  id: "t1",
  user: "加一台示波器，接在函数发生器的 CH2 上",
  reply: "",
  steps: [],
  files: [],
  live: [],
  status: "done",
  stop_reason: null,
  stop_question: null,
  stop_consent: null,
  compacted: false,
  rolled_back: false,
  started_at: "2026-11-10T08:00:00Z",
  finished_at: "2026-11-10T08:00:09Z",
};

const done: AssistantTurn = {
  ...base,
  reply: "已添加示波器 scope2，CH1 接在函数发生器的 CH2 上；测试台通过校验。",
  steps: [
    { action: "list_catalog", subject: "oscilloscope", ok: true, error: null },
    { action: "read_bench", subject: "hello.bench.yaml", ok: true, error: null },
    { action: "add_instrument", subject: "oscilloscope", ok: true, error: null },
    { action: "connect", subject: "awg.CH2 → scope2.CH1", ok: true, error: null },
  ],
  files: [
    {
      path: "hello.bench.yaml",
      kind: "bench",
      created: false,
      changes: [
        { kind: "instrument", action: "added", target: "scope2", detail: "model", old: null, new: "siglent-sds2104xplus" },
        { kind: "wire", action: "added", target: "awg.CH2 ↔ scope2.CH1", detail: null, old: null, new: null },
      ],
    },
  ],
};

const streaming: AssistantTurn = {
  ...base,
  id: "t2",
  user: "把频率改成 2 MHz",
  status: "running",
  finished_at: null,
  reply: "好的，正在把 CH1 的频率",
  steps: [
    { action: "read_bench", subject: "hello.bench.yaml", ok: true, error: null },
    { action: "set_state", subject: "awg.ch1.frequency", ok: null, error: null },
  ],
};

const stuck: AssistantTurn = {
  ...base,
  id: "t3",
  user: "删掉 ghost",
  status: "stuck",
  stop_reason: "连续 4 次遇到同一个错误，没有进展，已停下：第 1 个操作 remove：没有名为 'ghost' 的仪表或元件",
  steps: [{ action: "remove", subject: "ghost", ok: false, error: "第 1 个操作 remove：没有名为 'ghost' 的仪表或元件" }],
};

function Panel(props: Partial<AssistantViewProps>) {
  return (
    <div className="h-[640px] w-[380px] border">
      <AssistantView
        turns={[]}
        configured
        onSend={() => {}}
        onStop={() => {}}
        onClear={() => {}}
        onSettings={() => {}}
        onClose={() => {}}
        onRollback={() => {}}
        {...props}
      />
    </div>
  );
}

const meta: Meta<typeof Panel> = { title: "Assistant/Panel", component: Panel };
export default meta;

type Story = StoryObj<typeof Panel>;

export const NotConfigured: Story = { args: { configured: false } };
export const Empty: Story = {};
export const Done: Story = { args: { turns: [done], rollbackDeletes: [] } };
export const Streaming: Story = { args: { turns: [done, streaming], live: streaming } };
export const Stuck: Story = { args: { turns: [done, stuck], compacted: true } };
export const Stopped: Story = {
  args: { turns: [{ ...streaming, status: "stopped", stop_reason: "已按你的要求停止", reply: "好的，正在把 CH1 的频率" }] },
};
