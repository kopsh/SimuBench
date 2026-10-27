import { ChevronLeft, CircleCheck, CircleX, Crosshair, Play, Search } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams } from "react-router-dom";
import type { Schemas } from "@/api/client";
import { useReplayRun, useRun } from "@/api/hooks";
import { Logo } from "@/components/app/Logo";
import { PreferencesMenu } from "@/components/app/PreferencesMenu";
import { ErrorState } from "@/components/app/StateViews";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { formatClock, formatEng } from "@/lib/format";
import { cn } from "@/lib/utils";

type RunEvent = Schemas["RunEvent"];
type Mismatch = Schemas["ReplayMismatch"];

const SHOWN = 2000;
const BENCH_LANE = "";

/**
 * A run record (D36): what happened, on a timeline per instrument in virtual time and as a list,
 * and a replay that compares every reply and points at the first one that differs.
 */
export function RunPage() {
  const { id = "" } = useParams();
  const { t } = useTranslation();
  const navigate = useNavigate();
  const run = useRun(id);
  const replay = useReplayRun(id);
  const [selected, setSelected] = useState<number | null>(null);
  const [filter, setFilter] = useState("");

  const events = useMemo(() => (run.data?.events ?? []).filter((e) => e.kind !== "advance"), [run.data]);
  const mismatches = useMemo(() => new Map((replay.data?.mismatches ?? []).map((m) => [m.seq, m])), [replay.data]);
  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase();
    const list = q ? events.filter((e) => describe(e, t).toLowerCase().includes(q) || (e.instrument ?? "").toLowerCase().includes(q)) : events;
    return list.slice(0, SHOWN);
  }, [events, filter, t]);

  if (run.isLoading) return <Skeleton className="m-6 h-96" />;
  if (run.error || !run.data) return <ErrorState error={run.error ?? new Error("not found")} onRetry={() => void run.refetch()} />;
  const { summary } = run.data;
  const first = replay.data?.mismatches[0];

  return (
    <div className="flex h-full flex-col">
      <header className="bg-sidebar flex h-11 shrink-0 items-center gap-2 border-b px-2">
        <Button variant="ghost" size="icon" className="size-8" onClick={() => navigate("/")} aria-label={t("workbench.backHome")}>
          <ChevronLeft className="size-4" />
        </Button>
        <Logo className="hidden sm:inline" />
        <div className="ml-2 min-w-0">
          <div className="truncate text-[13px] leading-tight font-medium">
            {t("runs.run")} · {summary.bench}
          </div>
          <div className="text-muted-foreground num truncate text-[11px] leading-tight">{summary.run_id}</div>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <Button size="sm" className="h-8" onClick={() => replay.mutate()} disabled={replay.isPending}>
            <Play className="size-3.5" /> {replay.isPending ? t("runs.replaying") : t("runs.replay")}
          </Button>
          <PreferencesMenu />
        </div>
      </header>

      <main className="min-h-0 flex-1 space-y-3 overflow-auto p-3">
        <section className="text-muted-foreground num flex flex-wrap gap-x-5 gap-y-1 text-xs">
          <span>{new Date(summary.started * 1000).toLocaleString()}</span>
          <span>{t(`workbench.clockMode.${summary.clock}`)}</span>
          <span>
            {t("runs.seed")} {summary.seed}
          </span>
          <span>t = {formatClock(summary.duration_ns)}</span>
          <span>{t("runs.events", { count: summary.events })}</span>
          <span>SimuBench {summary.engine}</span>
          {!summary.ended && <span className="text-ok">{t("runs.live")}</span>}
        </section>

        {replay.error && <ErrorState error={replay.error} />}
        {replay.data && (
          <section
            role="status"
            aria-label={t("runs.result")}
            className={cn("rounded-lg border px-4 py-3 text-sm", replay.data.ok ? "border-ok/40 bg-ok/5" : "border-error/40 bg-error/5")}
          >
            <div className="flex items-center gap-2 font-medium">
              {replay.data.ok ? <CircleCheck className="text-ok size-4" /> : <CircleX className="text-error size-4" />}
              {replay.data.ok
                ? t("runs.identical", { actions: replay.data.actions, replies: replay.data.replies })
                : t("runs.differ", { count: replay.data.mismatches.length, replies: replay.data.replies })}
              {first && (
                <Button size="sm" variant="outline" className="ml-auto h-7 gap-1.5 text-xs" onClick={() => setSelected(first.seq)}>
                  <Crosshair className="size-3.5" /> {t("runs.locate")}
                </Button>
              )}
            </div>
            {first && <MismatchDetail m={first} />}
            {replay.data.warnings.map((w) => (
              <p key={w} className="text-warn mt-1 text-xs">
                {w}
              </p>
            ))}
          </section>
        )}

        <section className="bg-card rounded-lg border">
          <header className="border-b px-3 py-2 text-xs font-medium">{t("runs.timeline")}</header>
          <Timeline
            events={events}
            instruments={summary.instruments}
            duration={summary.duration_ns}
            selected={selected}
            mismatches={mismatches}
            onSelect={setSelected}
          />
        </section>

        <section className="bg-card rounded-lg border">
          <header className="flex items-center gap-3 border-b px-3 py-2 text-xs">
            <span className="font-medium">{t("runs.list")}</span>
            <div className="relative ml-auto w-64">
              <Search className="text-muted-foreground absolute top-1/2 left-2 size-3.5 -translate-y-1/2" />
              <Input
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder={t("runs.filter")}
                aria-label={t("runs.filter")}
                className="h-7 pl-7 text-xs"
              />
            </div>
          </header>
          <EventList events={shown} selected={selected} mismatches={mismatches} onSelect={setSelected} />
          {(run.data.truncated || shown.length === SHOWN) && <p className="text-muted-foreground border-t px-3 py-1.5 text-[11px]">{t("runs.truncated")}</p>}
        </section>
      </main>
    </div>
  );
}

function MismatchDetail({ m }: { m: Mismatch }) {
  const { t } = useTranslation();
  return (
    <dl className="num mt-2 grid grid-cols-[6rem_1fr] gap-x-3 gap-y-1 text-xs">
      <dt className="text-muted-foreground">{t("runs.where")}</dt>
      <dd>
        #{m.seq} · {m.instrument} · t = {formatClock(m.t_ns)}
      </dd>
      {m.command && (
        <>
          <dt className="text-muted-foreground">{t("runs.after")}</dt>
          <dd className="break-all">{m.command}</dd>
        </>
      )}
      <dt className="text-muted-foreground">{t("runs.recorded")}</dt>
      <dd className="break-all">{m.expected}</dd>
      <dt className="text-muted-foreground">{t("runs.replayed")}</dt>
      <dd className="text-error break-all">{m.actual}</dd>
    </dl>
  );
}

const TONE: Record<string, string> = {
  write: "var(--primary)",
  read: "var(--muted-foreground)",
  state: "var(--status-warn)",
  error: "var(--status-error)",
  fault: "var(--status-error)",
  acquisition: "var(--status-ok)",
};

function tone(e: RunEvent): string {
  if (e.kind === "scpi") return TONE[e.dir ?? "write"];
  return TONE[e.kind] ?? "var(--muted-foreground)";
}

/** One lane per instrument (and one for the bench); every event is a mark at its virtual time. */
function Timeline({
  events,
  instruments,
  duration,
  selected,
  mismatches,
  onSelect,
}: {
  events: RunEvent[];
  instruments: string[];
  duration: number;
  selected: number | null;
  mismatches: Map<number, Mismatch>;
  onSelect: (seq: number) => void;
}) {
  const { t } = useTranslation();
  const lanes = [...instruments, ...(events.some((e) => !e.instrument) ? [BENCH_LANE] : [])];
  const W = 1000;
  const LANE = 26;
  const pad = { l: 96, r: 36, t: 8, b: 22 };
  const H = pad.t + lanes.length * LANE + pad.b;
  const span = Math.max(duration, 1);
  const x = (ns: number) => pad.l + (ns / span) * (W - pad.l - pad.r);
  const laneY = (name: string) => pad.t + lanes.indexOf(name) * LANE + LANE / 2;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * duration);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label={t("runs.timeline")} data-testid="run-timeline">
      {lanes.map((name) => (
        <g key={name || "bench"}>
          <line x1={pad.l} x2={W - pad.r} y1={laneY(name)} y2={laneY(name)} stroke="var(--border)" />
          <text x={pad.l - 8} y={laneY(name) + 4} textAnchor="end" className="num fill-muted-foreground text-[11px]">
            {name || t("runs.benchLane")}
          </text>
        </g>
      ))}
      {ticks.map((v) => (
        <text key={v} x={x(v)} y={H - 6} textAnchor="middle" className="num fill-muted-foreground text-[10px]">
          {formatEng(v / 1e9, "s", 3)}
        </text>
      ))}
      {events.map((e) => {
        const cx = x(e.t_ns);
        const cy = laneY(e.instrument ?? BENCH_LANE);
        const off = e.kind === "scpi" ? (e.dir === "read" ? 5 : -5) : 0;
        const bad = mismatches.has(e.seq);
        const active = e.seq === selected;
        return (
          <g key={e.seq} onClick={() => onSelect(e.seq)} className="cursor-pointer">
            <title>{`#${e.seq} ${formatClock(e.t_ns)} ${describe(e, t)}`}</title>
            {(bad || active) && <circle cx={cx} cy={cy} r={8} fill="none" stroke={bad ? "var(--status-error)" : "var(--primary)"} strokeWidth={2} />}
            {e.kind === "scpi" ? (
              <line x1={cx} x2={cx} y1={cy} y2={cy + off} stroke={tone(e)} strokeWidth={2} />
            ) : (
              <rect x={cx - 3} y={cy - 3} width={6} height={6} fill={tone(e)} transform={e.kind === "fault" ? `rotate(45 ${cx} ${cy})` : undefined} />
            )}
          </g>
        );
      })}
    </svg>
  );
}

function EventList({
  events,
  selected,
  mismatches,
  onSelect,
}: {
  events: RunEvent[];
  selected: number | null;
  mismatches: Map<number, Mismatch>;
  onSelect: (seq: number) => void;
}) {
  const { t } = useTranslation();
  const rowRef = useRef<HTMLTableRowElement | null>(null);
  useEffect(() => {
    rowRef.current?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [selected]);
  return (
    <div className="max-h-[50vh] overflow-auto">
      <table className="num w-full text-[11px]" data-testid="run-events">
        <thead className="bg-card text-muted-foreground sticky top-0">
          <tr className="border-b">
            <th className="w-14 px-2 py-1 text-right font-medium">#</th>
            <th className="w-28 px-2 py-1 text-left font-medium">{t("runs.time")}</th>
            <th className="w-24 px-2 py-1 text-left font-medium">{t("runs.instrument")}</th>
            <th className="w-20 px-2 py-1 text-left font-medium">{t("runs.kind")}</th>
            <th className="px-2 py-1 text-left font-medium">{t("runs.content")}</th>
          </tr>
        </thead>
        <tbody>
          {events.map((e) => {
            const bad = mismatches.get(e.seq);
            const active = e.seq === selected;
            return (
              <tr
                key={e.seq}
                ref={active ? rowRef : undefined}
                onClick={() => onSelect(e.seq)}
                aria-selected={active}
                className={cn("hover:bg-accent/50 cursor-pointer border-b last:border-0", active && "bg-accent", bad && "bg-error/10")}
              >
                <td className="text-muted-foreground px-2 py-0.5 text-right">{e.seq}</td>
                <td className="px-2 py-0.5">{formatClock(e.t_ns)}</td>
                <td className="px-2 py-0.5">{e.instrument ?? "—"}</td>
                <td className="px-2 py-0.5">
                  <span className="inline-flex items-center gap-1.5">
                    <span className="size-1.5 rounded-full" style={{ background: tone(e) }} />
                    {e.kind === "scpi" ? (e.dir === "read" ? "←" : "→") : t(`runs.kinds.${e.kind}`, { defaultValue: e.kind })}
                  </span>
                </td>
                <td className="px-2 py-0.5 break-all">
                  {describe(e, t)}
                  {bad && <span className="text-error ml-2">≠ {bad.actual}</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function describe(e: RunEvent, t: (k: string, o?: Record<string, unknown>) => string): string {
  if (e.kind === "scpi") {
    if (e.sha256) {
      const recipe = e.recipe != null ? ` · ${t("runs.fromAcquisition", { seq: e.recipe })}` : "";
      return `<${e.size} bytes · sha256 ${e.sha256.slice(0, 12)}…${recipe}>`;
    }
    return (e.text ?? `<${e.size ?? 0} bytes>`).trim();
  }
  if (e.kind === "state") return `${e.path}: ${JSON.stringify(e.old)} → ${JSON.stringify(e.new)}${e.source && e.source !== "scpi" ? ` (${e.source})` : ""}`;
  if (e.kind === "error") return `${e.code}, ${e.message ?? ""}`;
  if (e.kind === "fault") return `${e.reason ?? ""} · ${e.message ?? ""}`;
  if (e.kind === "acquisition") {
    const chans = Object.entries(e.channels ?? {}).map(([ch, c]) => {
      const r = c as { points: number; dt_s: number; triggered: boolean };
      return `CH${ch} ${r.points} × ${formatEng(r.dt_s, "s", 3)}${r.triggered ? "" : ` (${t("runs.autoTrigger")})`}`;
    });
    return chans.join(" · ");
  }
  return e.message ?? e.reason ?? "";
}
