// The assistant panel's presentation (#877): the turns, the composer and every state of them —
// not configured, empty, streaming, stopped, stuck, restored. Data comes in as props (stories render
// it as is); AssistantPanel wires it to the service.
import { Bot, CircleAlert, CircleCheck, CircleX, Loader2, Settings2, Sparkles, Square, Trash2, Undo2, X } from "lucide-react";
import { type KeyboardEvent, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { EmptyState } from "@/components/app/StateViews";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { ChangeLine } from "@/features/instrument/HistoryTab";
import { cn } from "@/lib/utils";
import { type AssistantStep, type AssistantTurn, canRollBack, stepKey, stopAsked } from "./model";

export interface AssistantViewProps {
  turns: AssistantTurn[];
  /** The running turn as streamed so far (replaces the last turn while it runs). */
  live?: AssistantTurn | null;
  configured: boolean;
  /** Some earlier content is kept only in compacted form for the LLM. */
  compacted?: boolean;
  sending?: boolean;
  /** `stopConsent`: the answer to the assistant's request to stop the bench (the buttons, #902). */
  onSend: (text: string, stopConsent?: boolean) => void;
  onStop: () => void;
  onClear: () => void;
  onSettings: () => void;
  onClose?: () => void;
  onRollback: (turn: AssistantTurn) => void;
  /** The restore button's label names the files it deletes (from the plan); empty: none. */
  rollbackDeletes?: string[];
}

export function AssistantView(props: AssistantViewProps) {
  const { t } = useTranslation();
  const { turns, live, configured, onSettings, onClose, onClear } = props;
  const shown = live ? [...turns.slice(0, -1), live] : turns;
  const running = shown.at(-1)?.status === "running";
  const end = useRef<HTMLDivElement>(null);
  const lastReply = shown.at(-1)?.reply.length ?? 0;
  const lastSteps = shown.at(-1)?.steps.length ?? 0;
  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "end" });
  }, [shown.length, lastReply, lastSteps]);

  return (
    <aside className="bg-sidebar flex h-full min-h-0 flex-col" aria-label={t("assistant.title")} data-testid="assistant-panel">
      <header className="flex h-9 shrink-0 items-center gap-1 border-b pr-1 pl-3">
        <Sparkles className="text-primary size-3.5" aria-hidden />
        <h2 className="mr-auto text-xs font-medium">{t("assistant.title")}</h2>
        <IconButton label={t("assistant.clear")} disabled={running || turns.length === 0} onClick={onClear}>
          <Trash2 className="size-3.5" />
        </IconButton>
        <IconButton label={t("assistant.settings")} onClick={onSettings}>
          <Settings2 className="size-3.5" />
        </IconButton>
        {onClose && (
          <IconButton label={t("assistant.close")} onClick={onClose}>
            <X className="size-3.5" />
          </IconButton>
        )}
      </header>
      {!configured ? (
        <div className="flex flex-1 items-center">
          <EmptyState
            icon={<Bot className="size-6" />}
            title={t("assistant.notConfigured.title")}
            body={t("assistant.notConfigured.body")}
            action={
              <Button size="sm" onClick={onSettings}>
                <Settings2 className="size-3.5" /> {t("assistant.notConfigured.action")}
              </Button>
            }
          />
        </div>
      ) : (
        <>
          <div className="min-h-0 flex-1 overflow-y-auto px-3 py-2" aria-live="polite">
            {props.compacted && (
              <p className="text-muted-foreground mb-2 rounded border border-dashed px-2 py-1 text-center text-[11px]">
                {t("assistant.compacted")}
              </p>
            )}
            {shown.length === 0 ? (
              <div className="text-muted-foreground space-y-2 py-6 text-[12.5px] leading-relaxed">
                <p>{t("assistant.empty")}</p>
                <p>{t("assistant.examples")}</p>
              </div>
            ) : (
              shown.map((turn, i) => (
                <TurnView
                  key={turn.id}
                  turn={turn}
                  rollback={canRollBack(shown, i) ? () => props.onRollback(turn) : undefined}
                  rollbackDeletes={props.rollbackDeletes}
                />
              ))
            )}
            {stopAsked(shown) && (
              <StopQuestion
                reason={shown.at(-1)!.stop_question!}
                disabled={!!props.sending}
                onAnswer={(agree) =>
                  props.onSend(t(agree ? "assistant.stopAsk.agreeMessage" : "assistant.stopAsk.declineMessage"), agree)
                }
              />
            )}
            <div ref={end} />
          </div>
          <Composer running={running} sending={!!props.sending} onSend={props.onSend} onStop={props.onStop} />
        </>
      )}
    </aside>
  );
}

function IconButton({ label, children, ...rest }: { label: string; children: React.ReactNode; onClick: () => void; disabled?: boolean }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button variant="ghost" size="icon" className="size-7" aria-label={label} {...rest}>
          {children}
        </Button>
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  );
}

function TurnView({ turn, rollback, rollbackDeletes }: { turn: AssistantTurn; rollback?: () => void; rollbackDeletes?: string[] }) {
  const { t } = useTranslation();
  const ended = turn.status !== "running";
  return (
    <article className="mb-4 space-y-2" data-testid="assistant-turn">
      <div className="bg-background ml-6 rounded-md border px-2.5 py-1.5 text-[13px] leading-relaxed whitespace-pre-wrap">
        <span className="sr-only">{t("assistant.you")}: </span>
        {turn.user}
      </div>
      {turn.steps.length > 0 && (
        <ul className="space-y-0.5" aria-label={t("assistant.title")}>
          {turn.steps.map((s, i) => (
            <StepLine key={i} step={s} />
          ))}
        </ul>
      )}
      {turn.reply ? (
        <div className="text-[13px] leading-relaxed whitespace-pre-wrap" data-testid="assistant-reply">
          {turn.reply}
        </div>
      ) : (
        !ended && (
          <p className="text-muted-foreground flex items-center gap-1.5 text-xs">
            <Loader2 className="size-3 animate-spin" /> {t("assistant.thinking")}
          </p>
        )
      )}
      {turn.compacted && <p className="text-muted-foreground text-[11px]">{t("assistant.compacted")}</p>}
      {turn.status === "stopped" && <Notice tone="muted" text={t("assistant.stopped", { reason: turn.stop_reason ?? "" })} />}
      {turn.status === "stuck" && <Notice tone="warn" text={t("assistant.stuck", { reason: turn.stop_reason ?? "" })} />}
      {turn.status === "error" && <Notice tone="error" text={t("assistant.failed", { reason: turn.stop_reason ?? "" })} />}
      {ended && <Summary turn={turn} />}
      {turn.rolled_back && (
        <p className="text-muted-foreground flex items-center gap-1 text-[11px]">
          <Undo2 className="size-3" /> {t("assistant.rolledBack")}
        </p>
      )}
      {rollback && (
        <Button variant="outline" size="sm" className="h-7 max-w-full text-xs" onClick={rollback}>
          <Undo2 className="size-3.5" />
          <span className="truncate">
            {rollbackDeletes?.length
              ? t("assistant.rollback.buttonDeletes", {
                  files: rollbackDeletes.join("、"),
                })
              : t("assistant.rollback.button")}
          </span>
        </Button>
      )}
    </article>
  );
}

function StepLine({ step }: { step: AssistantStep }) {
  const { t } = useTranslation();
  const subject = step.subject ? t(`acceptance.class.${step.subject}`, { defaultValue: step.subject }) : "";
  const Icon = step.ok === null ? Loader2 : step.ok ? CircleCheck : CircleX;
  return (
    <li className="text-muted-foreground flex items-start gap-1.5 text-[11.5px]">
      <Icon
        className={cn("mt-0.5 size-3 shrink-0", step.ok === null && "animate-spin", step.ok === false && "text-warn", step.ok && "text-ok")}
        aria-hidden
      />
      <span className="min-w-0">
        {t(stepKey(step.action), { subject })}
        {step.error && <span className="text-foreground/80 block break-words">{step.error}</span>}
      </span>
    </li>
  );
}

function StopQuestion({ reason, disabled, onAnswer }: { reason: string; disabled: boolean; onAnswer: (agree: boolean) => void }) {
  const { t } = useTranslation();
  return (
    <div className="border-warn/40 bg-warn/10 mb-4 space-y-2 rounded border px-2.5 py-2 text-[12.5px]" role="group" aria-label={t("assistant.stopAsk.title")} data-testid="assistant-stop-question">
      <p className="font-medium">{t("assistant.stopAsk.title")}</p>
      <p className="break-words">{reason}</p>
      <p className="text-muted-foreground text-[11.5px]">{t("assistant.stopAsk.hint")}</p>
      <div className="flex gap-2">
        <Button size="sm" variant="destructive" className="h-7 text-xs" disabled={disabled} onClick={() => onAnswer(true)}>
          {t("assistant.stopAsk.agree")}
        </Button>
        <Button size="sm" variant="outline" className="h-7 text-xs" disabled={disabled} onClick={() => onAnswer(false)}>
          {t("assistant.stopAsk.decline")}
        </Button>
      </div>
    </div>
  );
}

function Notice({ tone, text }: { tone: "muted" | "warn" | "error"; text: string }) {
  return (
    <p
      role="status"
      className={cn(
        "flex items-start gap-1.5 rounded border px-2 py-1 text-[12px]",
        tone === "warn" && "border-warn/40 bg-warn/10",
        tone === "error" && "border-error/40 bg-error/10",
        tone === "muted" && "text-muted-foreground",
      )}
    >
      <CircleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
      <span className="min-w-0 break-words">{text}</span>
    </p>
  );
}

function Summary({ turn }: { turn: AssistantTurn }) {
  const { t } = useTranslation();
  if (turn.files.length === 0 && turn.live.length === 0) return null;
  return (
    <section className="bg-background rounded-md border px-2.5 py-1.5" aria-label={t("assistant.changed")} data-testid="assistant-summary">
      <h3 className="mb-1 text-[11px] font-medium">{t("assistant.changed")}</h3>
      {turn.files.map((f) => (
        <div key={f.path} className="mb-1 last:mb-0">
          <div className="num text-muted-foreground text-[11px] break-all">
            {f.created && <span className="text-foreground mr-1">{t("assistant.created")}</span>}
            {f.path}
          </div>
          <ul className="space-y-0.5 pl-2">
            {f.changes.slice(0, 12).map((c, i) => (
              <ChangeLine key={i} change={c} />
            ))}
          </ul>
        </div>
      ))}
      {turn.live.length > 0 && (
        <div className="mb-1 last:mb-0">
          <div className="text-muted-foreground text-[11px]">{t("assistant.liveChanges")}</div>
          <ul className="space-y-0.5 pl-2">
            {turn.live.slice(0, 12).map((c, i) => (
              <ChangeLine key={i} change={c} />
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function Composer({
  running,
  sending,
  onSend,
  onStop,
}: {
  running: boolean;
  sending: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
}) {
  const { t } = useTranslation();
  const [text, setText] = useState("");
  const send = () => {
    const value = text.trim();
    if (!value || running || sending) return;
    onSend(value);
    setText("");
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send();
    }
  };
  return (
    <div className="shrink-0 border-t p-2">
      <Textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKey}
        placeholder={t("assistant.placeholder")}
        aria-label={t("assistant.placeholder")}
        className="max-h-40 min-h-16 resize-none text-[13px]"
      />
      <div className="mt-1.5 flex justify-end">
        {running ? (
          <Button size="sm" variant="outline" className="h-7 text-xs" onClick={onStop}>
            <Square className="size-3" /> {t("assistant.stop")}
          </Button>
        ) : (
          <Button size="sm" className="h-7 text-xs" disabled={!text.trim() || sending} onClick={send}>
            {t("assistant.send")}
          </Button>
        )}
      </div>
    </div>
  );
}
