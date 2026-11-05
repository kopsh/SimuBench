import { describe, expect, it } from "vitest";

import { scpiOutcome } from "./hooks";

/** The one message a send path shows (issue 639): the link's error first — the exchange never
 * happened — then the first queued error. SpectrumScreen used to skip the link branch. */
describe("scpiOutcome", () => {
  const base = { response: null, errors: [], link_error: null, timed_out: false, now_ns: 0 };

  it("is ok when nothing was queued", () => {
    expect(scpiOutcome(base)).toEqual({ ok: true, message: null });
  });

  it("reports the first queued error with its code", () => {
    expect(scpiOutcome({ ...base, errors: [{ code: -113, message: "undefined header" }] })).toEqual({
      ok: false,
      message: "-113, undefined header",
    });
  });

  it("reports the link error before any queue content", () => {
    expect(scpiOutcome({ ...base, link_error: "connection lost" })).toEqual({
      ok: false,
      message: "connection lost",
    });
  });
});
