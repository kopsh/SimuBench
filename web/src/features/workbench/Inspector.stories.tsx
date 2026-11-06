import type { Meta, StoryObj } from "@storybook/react-vite";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { InstrumentInfo, StateVarInfo } from "@/api/client";
import { InstrumentInspector } from "./Inspector";

/**
 * The inspector of a 4-channel oscilloscope built from the template (#728): the settings the
 * definition did not name (comm.header, waveform.*, trigger.hold, the eight measurement slots)
 * show as paths in mono and stay folded; the summary lists lead, one channel shows at a time, and
 * an empty measurement slot reads "无 / None" and "—" instead of NULL and a blank.
 */
const v = (path: string, rest: Record<string, unknown>, channel: number | null = null) =>
  ({ path, channel, readonly: false, dangerous: false, ...rest }) as unknown as StateVarInfo;

function scope(): InstrumentInfo {
  const state: StateVarInfo[] = [
    v("timebase.scale", { type: "float", unit: "S", value: 2e-4, label: "时基", label_en: "Timebase" }),
    v("trigger.source", { type: "enum", options: ["C1", "C2", "C3", "C4"], value: "C1", label: "触发源", label_en: "Trigger source" }),
    v("trigger.hold", { type: "enum", options: ["OFF", "TI", "PS"], value: "OFF" }),
    v("comm.header", { type: "enum", options: ["OFF", "SHORT", "LONG"], value: "SHORT" }),
    v("waveform.sp", { type: "int", value: 0 }),
    v("waveform.np", { type: "int", value: 0 }),
  ];
  for (let i = 1; i <= 8; i++) {
    state.push(v(`measure.p${i}.kind`, { type: "enum", options: ["NULL", "PKPK", "FREQ", "RMS"], value: "NULL" }));
    state.push(v(`measure.p${i}.source`, { type: "enum", options: ["C1", "C2", "C3", "C4"], value: "" }));
  }
  for (let n = 1; n <= 4; n++) {
    state.push(v(`ch${n}.display`, { type: "bool", value: n === 1, label: "显示", label_en: "Trace" }, n));
    state.push(v(`ch${n}.scale`, { type: "float", unit: "V", value: 0.5, label: "垂直档位", label_en: "Scale" }, n));
    state.push(v(`ch${n}.coupling`, { type: "enum", options: ["DC", "AC", "GND"], value: "DC", label: "耦合", label_en: "Coupling" }, n));
    state.push(v(`ch${n}.skew`, { type: "float", unit: "S", value: 0 }, n));
  }
  return {
    name: "scope",
    label: "示波器",
    manufacturer: "Template",
    model: "T-4CH",
    idn: "Template,T-4CH,0,1.0",
    address_virtual: "TCPIP0::127.0.0.1::INSTR",
    remote: [],
    errors: [],
    channels: 4,
    state,
    display: { actions: {}, channel_summary: ["display", "scale", "coupling"], summary: ["timebase.scale", "trigger.source"], panel: null, panel_source: null, template: null },
  } as unknown as InstrumentInfo;
}

function InspectorColumn() {
  return (
    <QueryClientProvider client={new QueryClient()}>
      <div className="w-[320px] rounded-md border">
        <InstrumentInspector sid="" inst={scope()} readonly={false} />
      </div>
    </QueryClientProvider>
  );
}

const meta: Meta<typeof InspectorColumn> = { title: "Workbench/Inspector", component: InspectorColumn };
export default meta;
type Story = StoryObj<typeof InspectorColumn>;

export const FourChannelTemplateScope: Story = {};
