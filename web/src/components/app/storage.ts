import type { Schemas } from "@/api/client";

export type StorageEntry = Schemas["StorageEntry"];
export type StorageKind = StorageEntry["kind"];
export const STORAGE_KINDS: readonly StorageKind[] = ["history", "assistant", "files"];

/** Bytes in the same units as `simubench storage` (B, KiB, MiB, GiB; one decimal above bytes). */
export function formatBytes(n: number): string {
  let size = n;
  for (const unit of ["B", "KiB", "MiB"] as const) {
    if (size < 1024) return unit === "B" ? `${size.toFixed(0)} B` : `${size.toFixed(1)} ${unit}`;
    size /= 1024;
  }
  return `${size.toFixed(1)} GiB`;
}

/** Bytes per kind, every kind present (0 when it holds nothing). */
export function totalsByKind(entries: readonly StorageEntry[]): Record<StorageKind, number> {
  const out: Record<StorageKind, number> = { history: 0, assistant: 0, files: 0 };
  for (const e of entries) out[e.kind] += e.bytes;
  return out;
}

/** What a clean removes: orphans only, and their saved versions only when asked (#1238). */
export function doomed(entries: readonly StorageEntry[], history: boolean): StorageEntry[] {
  return entries.filter((e) => e.orphan && (history || e.kind !== "history"));
}

export function sumBytes(entries: readonly StorageEntry[]): number {
  return entries.reduce((n, e) => n + e.bytes, 0);
}
