import { describe, expect, it } from "vitest";
import { HISTORY_MAX, loadHistory, pushHistory, QUICK_COMMANDS, recentChips, RECENT_MAX, saveHistory } from "./commandHistory";

describe("command history", () => {
  it("puts the newest first, drops the earlier copy and keeps at most HISTORY_MAX", () => {
    expect(pushHistory(["b", "a"], "a")).toEqual(["a", "b"]);
    const many = Array.from({ length: HISTORY_MAX + 5 }, (_, i) => `c${i}`);
    expect(pushHistory(many, "new")).toHaveLength(HISTORY_MAX);
  });

  it("offers recent chips without the quick commands, at most RECENT_MAX", () => {
    const history = [QUICK_COMMANDS[0], ":A 1", ":B 2", ":C 3", ":D 4", ":E 5"];
    const chips = recentChips(history);
    expect(chips).toEqual([":A 1", ":B 2", ":C 3", ":D 4"]);
    expect(chips).toHaveLength(RECENT_MAX);
  });

  it("is kept per instrument and survives a reload; bad storage reads as empty", () => {
    saveHistory("dmm", [":A 1"]);
    saveHistory("psu", [":B 2"]);
    expect(loadHistory("dmm")).toEqual([":A 1"]);
    expect(loadHistory("psu")).toEqual([":B 2"]);
    localStorage.setItem("simubench.scpiHistory.bad", "{not json");
    expect(loadHistory("bad")).toEqual([]);
    expect(loadHistory("none")).toEqual([]);
  });
});
