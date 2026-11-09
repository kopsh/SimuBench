// Pure logic of the home page: one search box filters the benches and the instrument library.

interface BenchLike {
  name: string;
  path: string;
  description?: string | null;
}

interface InstrumentLike {
  id: string;
  manufacturer: string;
  model: string;
  instrument_class: string;
  description?: string | null;
}

function matches(q: string, ...fields: (string | null | undefined)[]): boolean {
  return !q || fields.some((f) => !!f && f.toLowerCase().includes(q));
}

/** Benches whose name, path or description contains the query; the open bench comes first. */
export function filterBenches<T extends BenchLike>(benches: readonly T[], query: string, openPath?: string | null): T[] {
  const q = query.trim().toLowerCase();
  const hits = benches.filter((b) => matches(q, b.name, b.path, b.description));
  if (!openPath) return hits;
  return [...hits.filter((b) => b.path === openPath), ...hits.filter((b) => b.path !== openPath)];
}

/**
 * Instruments matching the query, grouped by class. Groups keep the order of `classOrder` (the
 * Web's class list), unknown classes after it; inside a group by manufacturer, then model.
 */
export function groupInstruments<T extends InstrumentLike>(
  instruments: readonly T[],
  query: string,
  classOrder: readonly string[],
): { cls: string; items: T[] }[] {
  const q = query.trim().toLowerCase();
  const groups = new Map<string, T[]>();
  for (const i of instruments) {
    if (!matches(q, i.manufacturer, i.model, `${i.manufacturer} ${i.model}`, i.id, i.description, i.instrument_class)) continue;
    const list = groups.get(i.instrument_class) ?? [];
    list.push(i);
    groups.set(i.instrument_class, list);
  }
  const rank = (cls: string) => {
    const n = classOrder.indexOf(cls);
    return n < 0 ? classOrder.length : n;
  };
  return [...groups.entries()]
    .sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b))
    .map(([cls, items]) => ({
      cls,
      items: [...items].sort((a, b) => a.manufacturer.localeCompare(b.manufacturer) || a.model.localeCompare(b.model)),
    }));
}
