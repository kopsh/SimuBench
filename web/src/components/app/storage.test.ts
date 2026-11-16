import { describe, expect, it } from "vitest";
import { type StorageEntry, doomed, formatBytes, sumBytes, totalsByKind } from "./storage";

const entries: StorageEntry[] = [
  { kind: "files", name: "gone", bytes: 5000, orphan: true },
  { kind: "files", name: "kept", bytes: 1000, orphan: false },
  { kind: "history", name: "gone.bench.yaml", bytes: 105, orphan: true },
  { kind: "assistant", name: "gone.bench.yaml", bytes: 40, orphan: true },
];

describe("workspace data", () => {
  it("formats bytes like the command line", () => {
    expect([formatBytes(7), formatBytes(1536), formatBytes(5 * 1024 * 1024), formatBytes(3 * 1024 ** 3)]).toEqual([
      "7 B",
      "1.5 KiB",
      "5.0 MiB",
      "3.0 GiB",
    ]);
  });

  it("totals each kind, an empty kind included", () => {
    expect(totalsByKind(entries)).toEqual({ history: 105, assistant: 40, files: 6000 });
    expect(totalsByKind([])).toEqual({ history: 0, assistant: 0, files: 0 });
  });

  it("removes orphans only, and their history only when asked", () => {
    expect(doomed(entries, false).map((e) => `${e.kind}:${e.name}`)).toEqual(["files:gone", "assistant:gone.bench.yaml"]);
    expect(sumBytes(doomed(entries, true))).toBe(5145);
  });
});
