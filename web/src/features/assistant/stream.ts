// Following a turn's server-sent events (GET /api/assistant/events, #873). fetch, not EventSource:
// the access token (D43) goes in a header, never in the URL.
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
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
 */
export function useLiveTurn(bench: string, conversation: AssistantConversation | undefined) {
  const qc = useQueryClient();
  const [live, setLive] = useState<AssistantTurn | null>(null);
  const following = useRef<string | null>(null);
  const last = conversation?.turns.at(-1);
  const runningId = last?.status === "running" ? last.id : null;

  useEffect(() => {
    if (!runningId || following.current === runningId || !last) return;
    following.current = runningId;
    const abort = new AbortController();
    let current: AssistantTurn = { ...last, reply: "", steps: [] };
    setLive(current);
    const finish = () => {
      following.current = null;
      setLive(null);
      void qc.invalidateQueries({
        queryKey: assistantKeys.conversation(bench),
      });
      void qc.invalidateQueries({ queryKey: assistantKeys.rollback(bench) });
      void qc.invalidateQueries({ queryKey: keys.benchDoc(bench) });
      void qc.invalidateQueries({ queryKey: keys.sessions });
      void qc.invalidateQueries({ queryKey: keys.catalog });
    };
    followTurn(
      bench,
      runningId,
      (ev) => {
        current = applyEvent(current, ev);
        if (ev.type === "turn" && ev.turn) {
          qc.setQueryData<AssistantConversation>(assistantKeys.conversation(bench), (c) =>
            c
              ? {
                  ...c,
                  running: false,
                  turns: c.turns.map((t) => (t.id === ev.turn!.id ? ev.turn! : t)),
                }
              : c,
          );
        } else {
          setLive(current);
        }
        // The bench file changed under the canvas: show it at once.
        if (ev.type === "step_done" && ev.step?.ok) void qc.invalidateQueries({ queryKey: keys.benchDoc(bench) });
      },
      abort.signal,
    )
      .catch(() => undefined) // a dropped stream: the refetch below picks the turn up again
      .finally(() => {
        if (!abort.signal.aborted) finish();
      });
    return () => {
      abort.abort();
      following.current = null;
    };
    // `last` is read once per running turn id
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bench, runningId, qc]);

  return live;
}
