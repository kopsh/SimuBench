// Pure logic of the home page: one search box filters the benches and the instrument library;
// the library's class chips and counts come from the catalog, never from the copy (models are
// added and removed all the time).

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
  workspace?: boolean;
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

/** The library's class chips: each class present with its count, in the Web's class order
 * (unknown classes after it, alphabetically). */
export function classCounts<T extends InstrumentLike>(instruments: readonly T[], classOrder: readonly string[]): { cls: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const i of instruments) counts.set(i.instrument_class, (counts.get(i.instrument_class) ?? 0) + 1);
  const rank = (cls: string) => {
    const n = classOrder.indexOf(cls);
    return n < 0 ? classOrder.length : n;
  };
  return [...counts.entries()].sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b)).map(([cls, count]) => ({ cls, count }));
}

/** Instruments matching the query and the chosen class (null: all). The user's own definitions
 * come first, then by class order, manufacturer and model. */
export function filterInstruments<T extends InstrumentLike>(
  instruments: readonly T[],
  query: string,
  cls: string | null,
  classOrder: readonly string[],
): T[] {
  const q = query.trim().toLowerCase();
  const rank = (c: string) => {
    const n = classOrder.indexOf(c);
    return n < 0 ? classOrder.length : n;
  };
  return instruments
    .filter((i) => (cls === null || i.instrument_class === cls) && matches(q, i.model, i.manufacturer, `${i.manufacturer} ${i.model}`, i.id, i.description))
    .sort(
      (a, b) =>
        Number(!!b.workspace) - Number(!!a.workspace) ||
        rank(a.instrument_class) - rank(b.instrument_class) ||
        a.manufacturer.localeCompare(b.manufacturer) ||
        a.model.localeCompare(b.model),
    );
}
