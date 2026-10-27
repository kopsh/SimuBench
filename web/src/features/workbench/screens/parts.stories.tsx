import type { Meta, StoryObj } from "@storybook/react-vite";
import { Chip, Legend, Readout } from "./parts";

/** Instrument front-panel parts: large readouts with engineering units, status chips, legends. */
function Panel() {
  return (
    <div className="bg-card max-w-3xl rounded-lg border">
      <header className="flex flex-wrap items-center gap-2 border-b px-3 py-2 text-xs">
        <span className="font-medium">源测量单元</span>
        <span className="text-muted-foreground num">SB-SMU-1</span>
        <Chip on tone="ok">
          输出开
        </Chip>
        <Chip on tone="warn">
          电流限值
        </Chip>
        <Chip on={false} tone="error">
          过载
        </Chip>
        <span className="ml-auto flex gap-3">
          <Legend dashed={false} label="缓冲区" />
          <Legend dashed label="限值" />
        </span>
      </header>
      <div className="grid grid-cols-2 divide-x md:grid-cols-4">
        <Readout label="电压" value={3.99960004} unit="V" strong digits={6} />
        <Readout label="电流" value={0.004} unit="A" digits={6} />
        <Readout label="电阻" value={999.90001} unit="Ω" />
        <Readout label="θ" value={-44.99} unit="°" plain />
      </div>
    </div>
  );
}

const meta: Meta<typeof Panel> = { title: "Instruments/Front panel parts", component: Panel };
export default meta;
export const SmuPanel: StoryObj<typeof Panel> = {};
