import type { SessionInfo, StateVarInfo } from "./client";
import type { Event } from "./generated/events";

/**
 * Polling a session asks for values only (`state=values`, #1299): a scope's settings come with
 * their definitions (options, names, ranges), about 1 MB, and only the values move while the bench
 * runs. The values are laid onto the session already held. `null` when they cannot be: another
 * build of the bench (start, reset, rebuild), a definition changed at run time (`spec_rev`: a
 * power supply's series pair doubles a channel's limits, #1302), or instruments or settings that
 * do not match; the caller then fetches the full session.
 *
 * A setting whose value did not change keeps its object, so what renders from it does not re-render.
 */
export function withValues(held: SessionInfo, light: SessionInfo): SessionInfo | null {
  if (!held.build || held.build !== light.build || held.instruments.length !== light.instruments.length) return null;
  const instruments = [];
  for (const inst of light.instruments) {
    const before = held.instruments.find((i) => i.name === inst.name);
    const values = inst.values;
    if (!before || !values || before.definition_id !== inst.definition_id || before.spec_rev !== inst.spec_rev) return null;
    if (Object.keys(values).length !== before.state.length) return null;
    const state: StateVarInfo[] = [];
    for (const v of before.state) {
      if (!(v.path in values)) return null;
      const value = values[v.path];
      state.push(same(v.value, value) ? v : { ...v, value });
    }
    instruments.push({ ...inst, state, values: null });
  }
  return { ...light, instruments };
}

/**
 * A `state` event from the session's stream carries one value that changed (#1730): laid onto
 * the session already held, like the poll's `withValues` above but for a single variable. The
 * instrument or path the event names may be missing (the cache was fetched before the event, or
 * the change is in a part the cache does not hold): the held session comes back as it was, and
 * the debounced refetch settles the rest. A value that did not change keeps its object, so what
 * renders from it does not re-render.
 */
export function withStateEvent(held: SessionInfo, ev: Event): SessionInfo {
  if (!ev.instrument || !ev.path) return held;
  const at = held.instruments.findIndex((i) => i.name === ev.instrument);
  if (at === -1) return held;
  const inst = held.instruments[at];
  const vat = inst.state.findIndex((v) => v.path === ev.path);
  if (vat === -1) return held;
  const v = inst.state[vat];
  if (same(v.value, ev.new)) return held;
  const state = inst.state.slice();
  state[vat] = { ...v, value: ev.new };
  const instruments = held.instruments.slice();
  instruments[at] = { ...inst, state };
  return { ...held, instruments };
}

/** Values as JSON gives them: numbers, strings, booleans, null, and lists or objects of those. */
function same(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true;
  if (typeof a !== "object" || typeof b !== "object" || a === null || b === null) return false;
  return JSON.stringify(a) === JSON.stringify(b);
}
