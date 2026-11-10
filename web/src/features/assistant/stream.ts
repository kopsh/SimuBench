// Following a turn's server-sent events (GET /api/assistant/events, #873). fetch, not EventSource:
// the access token (D43) goes in a header, never in the URL.
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { ApiError } from "@/api/client";
import { assistantKeys, keys } from "@/api/hooks";
import { getToken } from "@/api/token";
import { type AssistantConversation, type AssistantEvent, type AssistantTurn, SseParser, applyEvent } from "./model";

export async function followTurn(
  bench: string,
  turn: string,
  onEvent: (ev: AssistantEvent) => void,
  signal: AbortSignal,
  after = 0,
): Promise<void> {
  const query = new URLSearchParams({ bench, turn, after: String(after) });
  const token = getToken();
  const res = await fetch(`/api/assistant/events?${query}`, {
    headers: token ? { "x-simubench-token": token } : {},
    signal,
  });
  if (!res.ok || !res.body) throw new ApiError(res.status, res.statusText);
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  const parser = new SseParser();
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    for (const ev of parser.feed(value)) onEvent(ev);
  }
}

/**
 * The live turn of a bench's conversation: while the last turn runs, its events are followed and
 * applied; when it ends, the conversation and everything the turn may have changed are refetched.
 * The server's conversation is the truth: it is polled while the turn runs, a live view of a turn
 * it no longer reports as running is dropped, and a stream that ends without the turn's final
 * event is followed again.
 */
export function useLiveTurn(bench: string, conversation: AssistantConversation | undefined) {
  const qc = useQueryClient();
  const [live, setLive] = useState<AssistantTurn | null>(null);
  const [attempt, setAttempt] = useState(0);
  const last = conversation?.turns.at(-1);
  const runningId = last?.status === "running" ? last.id : null;

  useEffect(() => {
    if (!runningId) return;
    const abort = new AbortController();
    const base = qc.getQueryData<AssistantConversation>(assistantKeys.conversation(bench))?.turns.find((t) => t.id === runningId);
    if (!base) return;
    let current: AssistantTurn = { ...base, reply: "", steps: [] };
    let ended = false;
    const refresh = () => {
      void qc.invalidateQueries({ queryKey: assistantKeys.conversation(bench) });
      void qc.invalidateQueries({ queryKey: assistantKeys.rollback(bench) });
      void qc.invalidateQueries({ queryKey: keys.benchDoc(bench) });
      void qc.invalidateQueries({ queryKey: keys.sessions });
      void qc.invalidateQueries({ queryKey: keys.catalog });
    };
    const poll = window.setInterval(() => void qc.invalidateQueries({ queryKey: assistantKeys.conversation(bench) }), 3000);
    followTurn(
      bench,
      runningId,
      (ev) => {
        current = applyEvent(current, ev);
        if (ev.type === "turn" && ev.turn) {
          ended = true;
          qc.setQueryData<AssistantConversation>(assistantKeys.conversation(bench), (c) =>
            c ? { ...c, running: false, turns: c.turns.map((t) => (t.id === ev.turn!.id ? ev.turn! : t)) } : c,
          );
        } else {
          setLive(current);
        }
        // The bench file changed under the canvas: show it at once.
        if (ev.type === "step_done" && ev.step?.ok) void qc.invalidateQueries({ queryKey: keys.benchDoc(bench) });
      },
      abort.signal,
    )
      .catch(() => undefined)
      .finally(() => {
        if (abort.signal.aborted) return;
        refresh();
        // Dropped before the end: follow it again (the refetch ends it if the turn is over).
        if (!ended) window.setTimeout(() => setAttempt((a) => a + 1), 1000);
      });
    return () => {
      abort.abort();
      window.clearInterval(poll);
    };
  }, [bench, runningId, attempt, qc]);

  // Only the turn the server still reports as running is shown live.
  return live && live.id === runningId ? live : null;
}
