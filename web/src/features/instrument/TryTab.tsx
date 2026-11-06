import { CornerDownLeft, Eraser, Power } from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ErrorItem, InstrumentInfo } from "@/api/client";
import { useQueryClient } from "@tanstack/react-query";
import { keys, useTryScpi } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { formatClock } from "@/lib/format";
import { cn } from "@/lib/utils";

interface Entry {
  command: string;
  response: string | null;
  errors: ErrorItem[];
  linkError: string | null;
  timedOut: boolean;
  seconds: number;
}

/** Try the instrument alone: send commands to a temporary session, read the replies and the error
 * queue, power-cycle it (T2/T4; instrument-authoring.md 7.2). */
export function TryTab({
  sid,
  inst,
  error,
  onReopen,
}: {
  sid: string | undefined;
  inst: InstrumentInfo | undefined;
  error: string | null;
  onReopen: () => Promise<string | undefined>;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const scpi = useTryScpi(sid);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [nowNs, setNowNs] = useState(0);
  const list = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = list.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [entries]);

  const send = async (e: FormEvent) => {
    e.preventDefault();
    const command = input.trim();
    if (!command || !sid || busy) return;
    setBusy(true);
    setInput("");
    const started = performance.now();
    try {
      const reply = await scpi.mutateAsync(command);
      setEntries((prev) => [
        ...prev,
        {
          command,
          response: reply.response ?? null,
          errors: reply.errors,
          linkError: reply.link_error,
          timedOut: reply.timed_out,
          seconds: (performance.now() - started) / 1000,
        },
      ]);
      setNowNs(reply.now_ns);
      // The overview and the error queue follow the command just sent.
      void qc.invalidateQueries({ queryKey: keys.tryInstrument(sid) });
    } catch (err) {
      setEntries((prev) => [
        ...prev,
        {
          command,
          response: null,
          errors: [{ code: 0, message: (err as Error).message }],
          linkError: null,
          timedOut: false,
          seconds: 0,
        },
      ]);
    } finally {
      setBusy(false);
    }
  };

  const powerCycle = async () => {
    const next = await onReopen();
    if (next) {
      setEntries([]);
      setNowNs(0);
    }
  };

  return (
    <div className="grid h-full min-h-0 gap-3 p-4 xl:grid-cols-[1fr_20rem]">
      <section className="bg-card flex min-h-0 flex-col rounded-lg border">
        <header className="flex flex-wrap items-center gap-2 border-b px-3 py-2 text-xs font-medium">
          <span>{t("acceptance.console")}</span>
          <span className="text-muted-foreground num text-[11px]">
            {t("acceptance.virtualNow", { time: formatClock(nowNs) })}
          </span>
          <div className="ml-auto flex items-center gap-1">
            <Button variant="ghost" size="sm" className="h-7 px-2 text-[11px]" onClick={() => setEntries([])} disabled={entries.length === 0}>
              <Eraser className="size-3.5" /> {t("acceptance.clearConsole")}
            </Button>
            <Button variant="outline" size="sm" className="h-7 px-2 text-[11px]" onClick={() => void powerCycle()} disabled={!sid}>
              <Power className="size-3.5" /> {t("acceptance.powerCycle")}
            </Button>
          </div>
        </header>
        <div ref={list} className="min-h-0 flex-1 overflow-auto p-3 font-mono text-[11.5px] leading-relaxed">
          {entries.length === 0 && (
            <p className="text-muted-foreground max-w-lg font-sans text-xs">{t("acceptance.consoleHint")}</p>
          )}
          {entries.map((entry, i) => (
            <div key={`${entry.command}-${i}`} className="mb-2">
              <div className="flex items-baseline gap-2">
                <span className="text-muted-foreground select-none">›</span>
                <span className="num break-all">{entry.command}</span>
              </div>
              {entry.response !== null && <pre className="num break-words mt-0.5 ml-3 whitespace-pre-wrap">{entry.response}</pre>}
              {entry.linkError && <p className="text-error ml-3 text-[11px]">{t("fault.linkDown")}: {entry.linkError}</p>}
              {entry.timedOut && <p className="text-warn ml-3 text-[11px]">{t("acceptance.noReply")}</p>}
              {entry.errors.map((err, j) => (
                <p key={j} className="text-error ml-3 text-[11px]">
                  {err.code ? `${err.code}: ` : ""}
                  {err.message}
                </p>
              ))}
            </div>
          ))}
        </div>
        {error ? (
          <p className="text-error border-t px-3 py-2 text-[11px]">{t("acceptance.tryFailed", { error })}</p>
        ) : (
          <form onSubmit={send} className="flex items-center gap-2 border-t p-2">
            <Input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="*IDN?"
              aria-label={t("acceptance.commandInput")}
              className="num h-8 flex-1"
              disabled={!sid || busy}
              autoFocus
            />
            <Button type="submit" size="sm" className="h-8" disabled={!sid || busy || input.trim() === ""}>
              <CornerDownLeft className="size-3.5" /> {t("acceptance.send")}
            </Button>
          </form>
        )}
      </section>

      <div className="flex min-h-0 flex-col gap-3 overflow-auto">
        <section className="bg-card rounded-lg border">
          <header className="border-b px-3 py-2 text-xs font-medium">{t("acceptance.errorQueue")}</header>
          <div className="p-3">
            {!inst?.errors.length ? (
              <p className="text-muted-foreground text-xs">{t("acceptance.errorQueueEmpty")}</p>
            ) : (
              <ul className="space-y-1.5 font-mono text-[11px]">
                {inst.errors.map((err, i) => (
                  <li key={`${err.code}-${i}`} className={cn("break-words", err.code < 0 ? "text-error" : "text-warn")}>
                    {err.code}: {err.message}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
        <section className="bg-card rounded-lg border">
          <header className="border-b px-3 py-2 text-xs font-medium">{t("acceptance.trySession")}</header>
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 px-3 py-2 text-[11px]">
            <dt className="text-muted-foreground">{t("acceptance.session")}</dt>
            <dd className="num break-all">{sid ?? "—"}</dd>
            <dt className="text-muted-foreground">{t("acceptance.idn")}</dt>
            <dd className="num break-all">{inst?.idn ?? "—"}</dd>
          </dl>
          <p className="text-muted-foreground border-t px-3 py-2 text-[11px]">{t("acceptance.tryNote")}</p>
        </section>
      </div>
    </div>
  );
}
