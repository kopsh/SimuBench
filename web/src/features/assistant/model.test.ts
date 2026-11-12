import { describe, expect, it } from "vitest";
import { en } from "@/i18n/en";
import { zh } from "@/i18n/zh";
import { type AssistantTurn, STEP_ACTIONS, SseParser, applyEvent, canRollBack, rollbackSummary, stepKey } from "./model";

const turn = (over: Partial<AssistantTurn> = {}): AssistantTurn => ({
  id: "t1",
  user: "加一台示波器",
  reply: "",
  steps: [],
  files: [],
  live: [],
  status: "running",
  stop_reason: null,
  compacted: false,
  rolled_back: false,
  started_at: "2026-11-10T00:00:00Z",
  finished_at: null,
  ...over,
});

describe("applyEvent", () => {
  it("streams the answer, opens and closes steps, takes the final turn", () => {
    let t = turn();
    t = applyEvent(t, {
      seq: 1,
      type: "text",
      turn_id: "t1",
      text: "好的，",
      step: null,
      index: null,
      turn: null,
    });
    t = applyEvent(t, {
      seq: 2,
      type: "step",
      turn_id: "t1",
      text: null,
      index: 0,
      turn: null,
      step: {
        action: "add_instrument",
        subject: "oscilloscope",
        ok: null,
        error: null,
      },
    });
    expect(t.reply).toBe("好的，");
    expect(t.steps[0].ok).toBeNull();
    t = applyEvent(t, {
      seq: 3,
      type: "step_done",
      turn_id: "t1",
      text: null,
      index: 0,
      turn: null,
      step: {
        action: "add_instrument",
        subject: "oscilloscope",
        ok: true,
        error: null,
      },
    });
    expect(t.steps).toHaveLength(1);
    expect(t.steps[0].ok).toBe(true);
    t = applyEvent(t, {
      seq: 4,
      type: "compacted",
      turn_id: "t1",
      text: null,
      step: null,
      index: null,
      turn: null,
    });
    expect(t.compacted).toBe(true);
    const final = turn({ status: "done", reply: "好的，已添加。" });
    expect(
      applyEvent(t, {
        seq: 5,
        type: "turn",
        turn_id: "t1",
        text: null,
        step: null,
        index: null,
        turn: final,
      }),
    ).toBe(final);
  });
});

describe("steps", () => {
  it("names every step in both languages, and unknown tools generically", () => {
    for (const action of STEP_ACTIONS) {
      expect(zh.assistant.step[action], action).toBeTruthy();
      expect(en.assistant.step[action], action).toBeTruthy();
    }
    expect(stepKey("add_instrument")).toBe("assistant.step.add_instrument");
    expect(stepKey("brand_new_tool")).toBe("assistant.step.other");
  });
});

describe("restore to before the last turn", () => {
  it("is offered on the last finished turn that changed files, once", () => {
    const changed = turn({
      status: "done",
      files: [{ path: "a.bench.yaml", kind: "bench", created: false, changes: [] }],
    });
    expect(canRollBack([changed], 0)).toBe(true);
    expect(canRollBack([changed, turn({ status: "done" })], 0)).toBe(false); // not the last turn
    expect(canRollBack([turn({ status: "done" })], 0)).toBe(false); // nothing changed
    expect(canRollBack([{ ...changed, status: "running" }], 0)).toBe(false);
    expect(canRollBack([{ ...changed, rolled_back: true }], 0)).toBe(false);
  });

  it("lists the files it deletes and the ones changed since", () => {
    const s = rollbackSummary({
      turn_id: "t1",
      sessions: [],
      files: [
        {
          path: "a.bench.yaml",
          kind: "bench",
          action: "restore",
          modified_since: true,
          lost: [],
        },
        {
          path: "a-copy.bench.yaml",
          kind: "bench",
          action: "delete",
          modified_since: false,
          lost: [],
        },
      ],
    });
    expect(s.deletes).toEqual(["a-copy.bench.yaml"]);
    expect(s.restores).toEqual(["a.bench.yaml"]);
    expect(s.modified.map((f) => f.path)).toEqual(["a.bench.yaml"]);
  });
});

describe("SseParser", () => {
  it("reads events split anywhere across chunks", () => {
    const p = new SseParser();
    const a = JSON.stringify({
      seq: 1,
      type: "text",
      turn_id: "t",
      text: "你",
    });
    const b = JSON.stringify({
      seq: 2,
      type: "text",
      turn_id: "t",
      text: "好",
    });
    const wire = `data: ${a}\n\ndata: ${b}\n\n`;
    expect(p.feed(wire.slice(0, 10))).toEqual([]);
    const first = p.feed(wire.slice(10, wire.indexOf("\n\n") + 5));
    expect(first.map((e) => e.text)).toEqual(["你"]);
    expect(p.feed(wire.slice(wire.indexOf("\n\n") + 5)).map((e) => e.text)).toEqual(["好"]);
  });
});
