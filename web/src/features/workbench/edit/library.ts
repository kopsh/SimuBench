import type { Catalog } from "@/api/client";

type LibraryInstrument = Catalog["instruments"][number];

/** The library's instruments by class, in `classOrder` (unknown classes last), each by model (#1356). */
export function libraryGroups(
  instruments: readonly LibraryInstrument[],
  classOrder: readonly string[],
): { cls: string; instruments: LibraryInstrument[] }[] {
  const rank = (c: string) => (classOrder.includes(c) ? classOrder.indexOf(c) : classOrder.length);
  const groups = new Map<string, LibraryInstrument[]>();
  for (const d of instruments) groups.set(d.instrument_class, [...(groups.get(d.instrument_class) ?? []), d]);
  return [...groups.entries()]
    .sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b))
    .map(([cls, list]) => ({ cls, instruments: list.sort((a, b) => a.model.localeCompare(b.model)) }));
}
