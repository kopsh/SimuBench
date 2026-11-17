import { describe, expect, it } from "vitest";
import type { Event } from "@/api/generated/events";
import i18n, { type T } from "@/i18n";
import { advanceReason, clockModeLabel, recentAdvances } from "./clockAdvances";

const advance = (seq: number, dt: number, reason: string, instrument?: string, header?: string): Event =>
  ({ seq, t_ns: seq * 1000, kind: "advance", dt_ns: dt, reason, instrument: instrument ?? null, header: header ?? null }) as Event;

describe("recentAdvances (#1260)", () => {
  it("lists the newest first and merges a burst of the same command", () => {
    const events = [
      advance(1, 110_000_000_000, "idle"),
      { seq: 2, t_ns: 2000, kind: "state" } as Event,
      advance(3, 5, "command", "scope", ":DIGitize"),
      advance(4, 7, "command", "scope", ":DIGitize"),
      advance(5, 3, "command", "sa", ":INIT"),
    ];
    const runs = recentAdvances(events);
    expect(runs.map((r) => [r.reason, r.instrument, r.count, r.dtNs])).toEqual([
      ["command", "sa", 1, 3],
      ["command", "scope", 2, 12],
      ["idle", null, 1, 110_000_000_000],
    ]);
  });
  it("keeps at most the limit", () => {
    const events = [1, 2, 3, 4].map((n) => advance(n, n, n % 2 ? "idle" : "explicit"));
    expect(recentAdvances(events, 2).map((r) => r.reason)).toEqual(["explicit", "idle"]);
  });
  it("says the cause in words, naming the command", () => {
    const t = i18n.getFixedT("zh") as T;
    expect(advanceReason({ reason: "command", instrument: "scope", header: ":DIGitize" }, t)).toContain("scope :DIGitize");
    expect(advanceReason({ reason: "idle", instrument: null, header: null }, t)).toContain("空闲");
  });
});

describe("clockModeLabel (#1360)", () => {
  const t = i18n.t.bind(i18n) as T;
  it("a command clock whose idle time follows the wall says so", () => {
    expect(clockModeLabel("command", "wall", t)).toBe("命令驱动 · 空闲按实时");
  });
  it("any other clock keeps its mode name", () => {
    expect(clockModeLabel("command", "skip", t)).toBe("命令驱动时钟");
    expect(clockModeLabel("stepped", "wall", t)).toBe("步进时钟");
  });
});
