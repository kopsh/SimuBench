// A DTO read model beside sessionValues.ts (#1735): the api layer serves the session shape, and
// this is the one accessor over it. It was features/workbench/model's before, which made the api
// module graph point up at the features (hooks.ts imported it for the readings wiring) — the
// eslint config bans `features` imports under src/api, so the direction cannot grow back.
import type { InstrumentInfo, StateVarInfo } from "./client";

/** A session instrument's state variables, keyed by path — the shape every panel and screen
 * reads settings by. */
export function stateMap(inst: InstrumentInfo): Map<string, StateVarInfo> {
  return new Map(inst.state.map((s) => [s.path, s]));
}
