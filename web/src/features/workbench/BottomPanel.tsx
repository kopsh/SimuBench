import { AlertCircle, AlertTriangle, ArrowDownToLine, ArrowLeft, ArrowRight, CircleCheck, Eraser, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { Diagnostic, SessionInfo } from "@/api/client";
import { useFaults } from "@/api/hooks";
import { FaultRow } from "./faults/FaultPanel";
import { useEvents } from "@/api/events";
import type { Event } from "@/api/generated/events";
import { EmptyState } from "@/components/app/StateViews";
import { VirtualList } from "@/components/app/VirtualList";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Toggle } from "@/components/ui/toggle";
import { formatClock, formatEng } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useWorkbench } from "./store";

const ROW = 24;

/** ``diagnostics`` are the bench file's; instrument error queues come from the session. */
export function BottomPanel({ session, diagnostics = [] }: { session: SessionInfo; diagnostics?: Diagnostic[] }) {
  const { t } = useTranslation();
  const { bottomTab, setBottomTab } = useWorkbench();
  const events = useEvents((s) => s.events);
  const clear = useEvents((s) => s.clear);
  const [filter, setFilter] = useState("");
  const [follow, setFollow] = useState(true);

  const activeFaults = (useFaults(session.id).data ?? []).filter((f) => f.status === "active");
  const problems = useMemo(
    () => session.instruments.flatMap((i) => i.errors.map((e) => ({ instrument: i.name, ...e }))),
    [session.instruments],
  );
  const q = filter.trim().toLowerCase();
  const comms = useMemo(
    () =>
      events.filter(
        (e) => e.kind === "scpi" && (!q || (e.text ?? "").toLowerCase().includes(q) || (e.instrument ?? "").toLowerCase().includes(q)),
      ),
    [events, q],
  );
  const others = useMemo(
    () =>
      events.filter(
        (e) => e.kind !== "scpi" && (!q || JSON.stringify(e).toLowerCase().includes(q)),
      ),
    [events, q],
  );

  return (
    <div className="bg-panel flex h-full flex-col">
      <div className="flex h-9 shrink-0 items-center gap-2 border-b px-2">
        <Tabs value={bottomTab} onValueChange={(v) => setBottomTab(v as typeof bottomTab)}>
          <TabsList className="h-7">
            <TabsTrigger value="comms" className="h-6 px-2.5 text-xs">
              {t("panel.comms")}
            </TabsTrigger>
            <TabsTrigger value="events" className="h-6 px-2.5 text-xs">
              {t("panel.events")}
            </TabsTrigger>
            <TabsTrigger value="problems" className="h-6 gap-1.5 px-2.5 text-xs">
              {t("panel.problems")}
              {problems.length + diagnostics.length + activeFaults.length > 0 && (
                <span
                  className={cn(
                    "num rounded px-1 text-[10px]",
                    problems.length || activeFaults.length || diagnostics.some((d) => d.severity === "error")
                      ? "bg-error/15 text-error"
                      : "bg-warn/15 text-warn",
                  )}
                >
                  {problems.length + diagnostics.length + activeFaults.length}
                </span>
              )}
            </TabsTrigger>
          </TabsList>
        </Tabs>
        <div className="relative ml-auto w-56">
          <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2" />
          <Input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder={t("panel.filter")} className="h-7 pl-7 text-xs" aria-label={t("panel.filter")} />
        </div>
        <Toggle size="sm" pressed={follow} onPressedChange={setFollow} className="h-7" aria-label={t("panel.autoscroll")} title={t("panel.autoscroll")}>
          <ArrowDownToLine className="size-3.5" />
        </Toggle>
        <Button variant="ghost" size="sm" className="h-7" onClick={clear} title={t("panel.clear")} aria-label={t("panel.clear")}>
          <Eraser className="size-3.5" />
        </Button>
      </div>
      {bottomTab === "comms" && (
        <>
          <div className="text-muted-foreground grid h-6 shrink-0 grid-cols-[96px_96px_64px_96px_28px_1fr_64px_72px] items-center gap-2 border-b px-3 text-[11px]">
            <span title={t("panel.timeHint")}>{t("panel.virtualTime")}</span>
            <span title={t("panel.wallTimeHint")}>{t("panel.wallTimeHeader")}</span>
            <span>{t("panel.source")}</span>
            <span>{t("panel.instrument")}</span>
            <span />
            <span>{t("panel.message")}</span>
            <span className="text-right">{t("panel.bytes")}</span>
            <span className="text-right">{t("panel.duration")}</span>
          </div>
          <VirtualList
            className="min-h-0 flex-1"
            items={comms}
            rowHeight={ROW}
            follow={follow}
            empty={<EmptyState title={t("panel.noComms")} />}
            render={(e) => <CommRow e={e} />}
          />
        </>
      )}
      {bottomTab === "events" && (
        <VirtualList
          className="min-h-0 flex-1"
          items={others}
          rowHeight={ROW}
          follow={follow}
          empty={<EmptyState title={t("panel.noEvents")} />}
          render={(e) => <EventRow e={e} />}
        />
      )}
      {bottomTab === "problems" && (
        <div className="min-h-0 flex-1 overflow-auto">
          {problems.length + diagnostics.length + activeFaults.length === 0 ? (
            <EmptyState icon={<CircleCheck className="text-ok size-6" />} title={t("panel.noProblems")} />
          ) : (
            <ul>
              {activeFaults.length > 0 && (
                <ul className="border-b px-3 py-1.5">
                  {activeFaults.map((f) => (
                    <FaultRow key={f.id} f={f} />
                  ))}
                </ul>
              )}
              {diagnostics.map((d, i) => (
                <DiagnosticRow key={`d${i}`} d={d} />
              ))}
              {problems.map((p, i) => (
                <ProblemRow key={i} instrument={p.instrument} code={p.code} message={p.message} />
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

function CommRow({ e }: { e: Event }) {
  const { t } = useTranslation();
  const select = useWorkbench((s) => s.select);
  const write = e.dir === "write";
  return (
    <div
      className={cn(
        "hover:bg-accent/60 grid h-full cursor-default grid-cols-[96px_96px_64px_96px_28px_1fr_64px_72px] items-center gap-2 px-3 text-[11.5px]",
        !write && "text-muted-foreground",
      )}
      onClick={() => e.instrument && select({ kind: "instrument", id: e.instrument })}
    >
      <span className="num text-muted-foreground">{formatClock(e.t_ns)}</span>
      <span className="num text-muted-foreground" data-testid="wall-time">
        {e.wall ? formatWall(e.wall) : ""}
      </span>
      <span className="text-muted-foreground truncate" title={e.client ?? ""}>
        {clientLabel(e.client, t)}
      </span>
      <span className="truncate">{e.instrument}</span>
      <span title={write ? t("panel.write") : t("panel.read")}>
        {write ? <ArrowRight className="text-primary size-3.5" /> : <ArrowLeft className="size-3.5" />}
      </span>
      <span className="num truncate" title={e.text ?? ""}>
        {(e.text ?? "").replace(/\n$/, "")}
      </span>
      <span className="num text-muted-foreground text-right">{e.size ?? ""}</span>
      <span className="num text-muted-foreground text-right">{e.duration_ns ? formatEng(e.duration_ns / 1e9, "s", 3) : ""}</span>
    </div>
  );
}

function EventRow({ e }: { e: Event }) {
  const { t } = useTranslation();
  const text =
    e.kind === "panel"
      ? `${e.instrument} ${t("panel.frontPanel")}: ${t(`scope.${e.action ?? "run"}`)}  (${e.source ?? ""})`
      : e.kind === "remote"
      ? `${e.instrument} ${t(e.action === "local" ? "remote.eventLocal" : "remote.eventRemote")}  (${e.source ?? ""})`
      : e.kind === "fault"
      ? `${e.fault} ${e.reason}  ${e.message ?? ""}  (${e.source ?? ""})`
      : e.kind === "state"
      ? `${e.instrument} ${e.path}: ${JSON.stringify(e.old)} → ${JSON.stringify(e.new)}  (${e.source})`
      : e.kind === "advance"
        ? `+${formatEng((e.dt_ns ?? 0) / 1e9, "s", 4)}  ${e.reason ?? ""}`
        : e.kind === "error"
          ? `${e.instrument}  ${e.code}, ${e.message}`
          : JSON.stringify(e);
  return (
    <div className="hover:bg-accent/60 grid h-full grid-cols-[96px_72px_1fr] items-center gap-2 px-3 text-[11.5px]">
      <span className="num text-muted-foreground">{formatClock(e.t_ns)}</span>
      <span className={cn("text-[11px] font-medium", e.kind === "error" || e.kind === "fault" ? "text-error" : "text-muted-foreground")}>
        {e.kind}
      </span>
      <span className="num truncate" title={text}>
        {text}
      </span>
    </div>
  );
}

function DiagnosticRow({ d }: { d: Diagnostic }) {
  const select = useWorkbench((s) => s.select);
  const subject = d.subject;
  const onClick = () => {
    if (!subject) return select(null);
    if (subject.startsWith("wire:")) return select({ kind: "wire", id: Number(subject.slice(5)) });
    select({ kind: d.pointer.startsWith("/parts/") ? "part" : "instrument", id: subject });
  };
  return (
    <li>
      <button className="hover:bg-accent/60 flex w-full items-start gap-2 px-3 py-1.5 text-left text-xs" onClick={onClick}>
        {d.severity === "error" ? (
          <AlertCircle className="text-error mt-0.5 size-3.5 shrink-0" />
        ) : (
          <AlertTriangle className="text-warn mt-0.5 size-3.5 shrink-0" />
        )}
        <span className={cn("num w-40 shrink-0 truncate", d.severity === "error" ? "text-error" : "text-warn")}>{d.code}</span>
        <span className="flex-1">
          {d.message}
          {d.suggestion && <span className="text-muted-foreground"> — {d.suggestion}</span>}
        </span>
        <span className="text-muted-foreground num shrink-0">{d.pointer}</span>
      </button>
    </li>
  );
}

function ProblemRow({ instrument, code, message }: { instrument: string; code: number; message: string }) {
  const select = useWorkbench((s) => s.select);
  return (
    <li>
      <button
        className="hover:bg-accent/60 flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs"
        onClick={() => select({ kind: "instrument", id: instrument })}
      >
        <AlertTriangle className="text-error size-3.5 shrink-0" />
        <span className="num text-error w-12">{code}</span>
        <span className="flex-1">{message}</span>
        <span className="text-muted-foreground">{instrument}</span>
      </button>
    </li>
  );
}

/** Who sent a message: the script, the UI, an agent, a network client or a replay. */
function clientLabel(client: string | null | undefined, t: (key: string) => string): string {
  if (!client) return "";
  if (client === "pyvisa" || client === "sdk") return t("panel.client.script");
  if (client === "ui" || client === "mcp" || client === "replay") return t(`panel.client.${client}`);
  if (client.startsWith("socket:")) return "SOCKET";
  if (client.startsWith("hislip:")) return "HiSLIP";
  return client;
}

/** Real local time of day with milliseconds, from epoch seconds. */
function formatWall(epochS: number): string {
  const d = new Date(epochS * 1000);
  const pad = (n: number, w = 2) => String(n).padStart(w, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${pad(d.getMilliseconds(), 3)}`;
}
