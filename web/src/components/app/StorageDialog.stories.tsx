import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";
import { StorageView } from "./StorageDialog";
import { type StorageEntry, sumBytes } from "./storage";

const meta: Meta = { title: "App/Workspace data" };
export default meta;

const entries: StorageEntry[] = [
  { kind: "history", name: "instruments/keysight-uxr1104b.yaml", bytes: 48_000_000, orphan: false },
  { kind: "files", name: "benches/old-adc-test", bytes: 12_400_000, orphan: true },
  { kind: "history", name: "benches/old-adc-test.bench.yaml", bytes: 412_000, orphan: true },
  { kind: "files", name: "hello", bytes: 220_000, orphan: false },
  { kind: "history", name: "hello.bench.yaml", bytes: 96_000, orphan: false },
  { kind: "assistant", name: "benches/old-adc-test.bench.yaml", bytes: 38_000, orphan: true },
  { kind: "assistant", name: "hello.bench.yaml", bytes: 12_000, orphan: false },
];
const total = sumBytes(entries);
const kept = entries.filter((e) => !e.orphan);

function Live({ start = false }: { start?: boolean }) {
  const [history, setHistory] = useState(false);
  const [confirming, setConfirming] = useState(start);
  return (
    <div className="w-[30rem] p-4">
      <StorageView
        entries={entries}
        total={total}
        history={history}
        onHistory={setHistory}
        confirming={confirming}
        onConfirming={setConfirming}
        busy={false}
        onClean={() => setConfirming(false)}
      />
    </div>
  );
}

export const WithOrphans: StoryObj = { render: () => <Live /> };
export const Confirming: StoryObj = { render: () => <Live start /> };
export const NothingLost: StoryObj = {
  render: () => (
    <div className="w-[30rem] p-4">
      <StorageView
        entries={kept}
        total={sumBytes(kept)}
        history={false}
        onHistory={() => undefined}
        confirming={false}
        onConfirming={() => undefined}
        busy={false}
        onClean={() => undefined}
      />
    </div>
  ),
};
