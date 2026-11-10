// The assistant panel's pure logic (#877): applying stream events to a turn, the wording of a
// progress step, reading the server-sent event stream. Components stay thin; this is unit-tested.
import type { Schemas } from "@/api/client";

export type AssistantTurn = Schemas["AssistantTurn"];
export type AssistantStep = Schemas["AssistantStep"];
export type AssistantEvent = Schemas["AssistantEvent"];
export type AssistantConversation = Schemas["AssistantConversation"];
export type RollbackPlan = Schemas["RollbackPlan"];
export type RollbackFile = Schemas["RollbackFile"];

/** The turn after one event: text appended, a step started or finished, or the final turn. */
export function applyEvent(turn: AssistantTurn, ev: AssistantEvent): AssistantTurn {
  switch (ev.type) {
    case "text":
      return { ...turn, reply: turn.reply + (ev.text ?? "") };
    case "step":
    case "step_done": {
      if (!ev.step || ev.index === null || ev.index === undefined) return turn;
      const steps = [...turn.steps];
      steps[ev.index] = ev.step;
      return { ...turn, steps };
    }
    case "compacted":
      return { ...turn, compacted: true };
    case "turn":
      return ev.turn ?? turn;
    default:
      return turn;
  }
}

/** Steps the panel names by what they do; any other tool falls back to a generic line. */
export const STEP_ACTIONS = [
  "list_catalog",
  "get_definition",
  "list_benches",
  "read_bench",
  "create_bench",
  "copy_bench",
  "validate_bench",
  "start_session",
  "ask_to_stop_session",
  "stop_session",
  "describe_session",
  "get_instrument_state",
  "set_live_setting",
  "read_definition",
  "save_template",
  "edit_definition",
  "validate_definition",
  "try_definition",
  "run_cases",
  "add_instrument",
  "add_part",
  "add_dut",
  "remove",
  "replace_instrument",
  "rename",
  "connect",
  "disconnect",
  "set_state",
  "set_params",
  "set_accessories",
  "set_label",
  "set_address",
  "set_identity",
  "set_deviation",
  "set_dut",
  "move",
  "set_meta",
  "set_run",
  "edit_bench",
] as const;
export type StepAction = (typeof STEP_ACTIONS)[number];

export function stepKey(action: string): `assistant.step.${StepAction}` | "assistant.step.other" {
  return (STEP_ACTIONS as readonly string[]).includes(action) ? `assistant.step.${action as StepAction}` : "assistant.step.other";
}

/** Whether the turn can still change: the panel shows the stop button and follows its events. */
export const isRunning = (turn: AssistantTurn | undefined) => turn?.status === "running";

/** "Restore to before the last turn" is offered on the last turn only, when it changed files. */
export function canRollBack(turns: AssistantTurn[], index: number): boolean {
  const turn = turns[index];
  return index === turns.length - 1 && !!turn && turn.status !== "running" && !turn.rolled_back && turn.files.length > 0;
}

/** Files the restore deletes (the turn created them) and restores, for the button and the confirmation. */
export function rollbackSummary(plan: RollbackPlan) {
  return {
    deletes: plan.files.filter((f) => f.action === "delete").map((f) => f.path),
    restores: plan.files.filter((f) => f.action === "restore").map((f) => f.path),
    modified: plan.files.filter((f) => f.modified_since),
  };
}

/**
 * Read a server-sent event stream: each `data:` line holds one JSON event. Chunks may split a line
 * anywhere; `feed` keeps the unfinished tail for the next chunk.
 */
export class SseParser {
  private buffer = "";
  feed(chunk: string): AssistantEvent[] {
    this.buffer += chunk;
    const out: AssistantEvent[] = [];
    let cut: number;
    while ((cut = this.buffer.indexOf("\n\n")) >= 0) {
      const block = this.buffer.slice(0, cut);
      this.buffer = this.buffer.slice(cut + 2);
      for (const line of block.split("\n")) {
        if (line.startsWith("data:")) out.push(JSON.parse(line.slice(5).trim()) as AssistantEvent);
      }
    }
    return out;
  }
}
