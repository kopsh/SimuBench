import { ArrowRight, ChevronRight, FileWarning, FolderOpen, Library, Plug, Search, SearchX, Workflow } from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import type { BenchFile, SessionInfo } from "@/api/client";
import { useBenches, useCatalog, useSessions, useStartSession } from "@/api/hooks";
import { isCancelled } from "@/components/app/ClientsConfirm";
import { Logo } from "@/components/app/Logo";
import { PreferencesMenu } from "@/components/app/PreferencesMenu";
import { EmptyState, ErrorState } from "@/components/app/StateViews";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Skeleton } from "@/components/ui/skeleton";
import { formatClock } from "@/lib/format";
import { CLASS_IDS, ClassIcon } from "@/features/workbench/classes";
import { cn } from "@/lib/utils";
import { RecentRuns } from "../runs/RecentRuns";
import { GettingStarted } from "./GettingStarted";
import { filterBenches, groupInstruments } from "./model";
import { NewBenchDialog } from "./NewBenchDialog";

export function HomePage() {
  const { t } = useTranslation();
  const benches = useBenches();
  const catalog = useCatalog();
  const sessions = useSessions();
  const start = useStartSession();
  const navigate = useNavigate();
  const [filter, setFilter] = useState("");
  const [pendingSwitch, setPendingSwitch] = useState<BenchFile | null>(null);

  // One bench is open at a time (it holds the SCPI ports); a bench opens on click (issue 31).
  const open = (sessions.data ?? [])[0];
  const allBenches = benches.data ?? [];
  const files = useMemo(() => filterBenches(benches.data ?? [], filter, open?.bench_path), [benches.data, filter, open?.bench_path]);
  const instruments = catalog.data?.instruments ?? [];
  const groups = useMemo(() => groupInstruments(catalog.data?.instruments ?? [], filter, CLASS_IDS), [catalog.data, filter]);

  const openBench = (b: BenchFile) => {
    if (open && open.bench_path !== b.path && open.clients.length > 0) {
      setPendingSwitch(b); // scripts are connected: ask before cutting the ports
      return;
    }
    startBench(b);
  };

  // ``force`` once the user confirmed dropping the scripts below; otherwise the engine asks (#121).
  const startBench = (b: BenchFile, force = false) =>
    start.mutate(
      { bench_path: b.path, force },
      {
        onSuccess: (s) => navigate(`/s/${s.id}`),
        onError: (e) => !isCancelled(e) && toast.error(t("errors.generic"), { description: (e as Error).message }),
      },
    );

  const error = benches.error ?? sessions.error;

  return (
    <div className="flex h-full flex-col">
      <header className="bg-sidebar flex h-11 shrink-0 items-center gap-3 border-b px-4">
        <Logo />
        <span className="text-muted-foreground hidden text-xs sm:inline">{t("app.tagline")}</span>
        <div className="ml-auto">
          <PreferencesMenu />
        </div>
      </header>
      <div className="flex-1 overflow-auto">
        <div className="home-hero relative border-b">
          <HeroTrace />
          <div className="relative mx-auto flex w-full max-w-6xl flex-col gap-5 px-6 pt-8 pb-6">
            <div className="flex flex-wrap items-end gap-x-8 gap-y-3">
              <div className="min-w-0">
                <h1 className="text-2xl font-semibold tracking-tight">{t("home.title")}</h1>
                <p className="text-muted-foreground mt-1 max-w-xl text-[13.5px]">{t("home.subtitle")}</p>
              </div>
              <dl className="ml-auto flex gap-6" aria-label={t("home.statsLabel")}>
                <Stat value={benches.isLoading ? undefined : allBenches.length} label={t("home.statBenches")} />
                <Stat value={catalog.isLoading ? undefined : instruments.length} label={t("home.statInstruments")} />
              </dl>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <div className="relative min-w-0 flex-1 sm:max-w-md">
                <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2" />
                <Input
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                  onKeyDown={(e) => e.key === "Escape" && setFilter("")}
                  placeholder={t("home.filter")}
                  className="bg-card h-9 pl-9 shadow-xs"
                  aria-label={t("home.filter")}
                />
              </div>
              <NewBenchDialog />
            </div>
          </div>
        </div>

        <main className="mx-auto w-full max-w-6xl px-6 pt-6 pb-12">
          <GettingStarted example={allBenches.find((b) => !b.error && b.instruments > 0)} onStart={openBench} />

          {error ? (
            <ErrorState
              error={error}
              onRetry={() => {
                void benches.refetch();
                void sessions.refetch();
              }}
            />
          ) : (
            <div className="mt-6 grid gap-8 lg:grid-cols-[minmax(0,1fr)_320px]">
              <div className="min-w-0">
                <section aria-labelledby="home-benches">
                  <SectionTitle id="home-benches" title={t("home.files")} count={benches.isLoading ? undefined : files.length} />
                  {benches.isLoading ? (
                    <div className="grid gap-3 sm:grid-cols-2">
                      {[0, 1, 2, 3].map((i) => (
                        <Skeleton key={i} className="h-32 w-full rounded-xl" />
                      ))}
                    </div>
                  ) : allBenches.length === 0 ? (
                    <div className="rounded-xl border border-dashed">
                      <EmptyState icon={<FolderOpen className="size-6" />} title={t("home.emptyTitle")} body={t("home.emptyBody")} />
                    </div>
                  ) : files.length === 0 ? (
                    <div className="rounded-xl border border-dashed">
                      <EmptyState
                        icon={<SearchX className="size-6" />}
                        title={t("home.noMatch", { query: filter.trim() })}
                        action={
                          <Button variant="outline" size="sm" onClick={() => setFilter("")}>
                            {t("home.clearFilter")}
                          </Button>
                        }
                      />
                    </div>
                  ) : (
                    <ul className="grid gap-3 sm:grid-cols-2" data-testid="bench-list">
                      {files.map((b) => {
                        const session = open && open.bench_path === b.path ? open : undefined;
                        return (
                          <BenchCard
                            key={b.path}
                            bench={b}
                            session={session}
                            busy={start.isPending}
                            onOpen={() => (session ? navigate(`/s/${session.id}`) : openBench(b))}
                          />
                        );
                      })}
                    </ul>
                  )}
                </section>
                <RecentRuns />
              </div>

              <aside aria-labelledby="home-instruments" className="lg:sticky lg:top-6 lg:self-start">
                <section className="bg-card overflow-hidden rounded-xl border" data-testid="instrument-list">
                  <div className="border-b px-4 py-3">
                    <h2 id="home-instruments" className="flex items-center gap-2 text-sm font-semibold">
                      <Library className="text-muted-foreground size-4" />
                      {t("home.instrumentsTitle")}
                      <span className="text-muted-foreground num ml-auto text-xs font-normal">{instruments.length}</span>
                    </h2>
                    <p className="text-muted-foreground mt-0.5 text-[11.5px] leading-snug">{t("home.instrumentsHint")}</p>
                  </div>
                  <div className="lg:max-h-[calc(100vh-11rem)] lg:overflow-y-auto">
                    {catalog.isLoading ? (
                      <div className="space-y-2 p-4">
                        {[0, 1, 2, 3, 4].map((i) => (
                          <Skeleton key={i} className="h-6 w-full" />
                        ))}
                      </div>
                    ) : groups.length === 0 ? (
                      <p className="text-muted-foreground px-4 py-6 text-center text-xs">{t("home.noInstrumentMatch")}</p>
                    ) : (
                      groups.map((g) => (
                        <div key={g.cls} className="border-b py-1.5 last:border-b-0">
                          <h3 className="text-muted-foreground flex items-center gap-1.5 px-4 py-1 text-[11px] font-medium">
                            <ClassIcon cls={g.cls} className="size-3.5" />
                            {t(`acceptance.class.${g.cls}`, { defaultValue: g.cls })}
                            <span className="num ml-auto">{g.items.length}</span>
                          </h3>
                          <ul>
                            {g.items.map((i) => (
                              <li key={i.id}>
                                <Link
                                  to={`/i/${i.id}`}
                                  title={i.description || i.id}
                                  className="hover:bg-accent/60 focus-visible:bg-accent/60 group flex h-7 items-center gap-2 px-4 text-[12.5px] outline-none transition-colors"
                                >
                                  {/* The model leads (it is what people look for); the maker gives way when space runs out. */}
                                  <span className="flex min-w-0 flex-1 items-baseline gap-1.5">
                                    <span className="shrink-0 font-medium">{i.model}</span>
                                    <span className="text-muted-foreground min-w-0 truncate text-[11.5px]">{i.manufacturer}</span>
                                  </span>
                                  {i.template && (
                                    <span className="text-muted-foreground shrink-0 text-[10px]">{t("acceptance.templateBadge")}</span>
                                  )}
                                  {i.channels > 1 && (
                                    <span className="text-muted-foreground num shrink-0 text-[10.5px]">{t("home.channelCount", { count: i.channels })}</span>
                                  )}
                                  <ChevronRight className="text-muted-foreground size-3.5 opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100" />
                                </Link>
                              </li>
                            ))}
                          </ul>
                        </div>
                      ))
                    )}
                  </div>
                </section>
              </aside>
            </div>
          )}
        </main>
      </div>

      <Dialog open={!!pendingSwitch} onOpenChange={(v) => !v && setPendingSwitch(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("home.switchTitle")}</DialogTitle>
            <DialogDescription>{t("home.switchBody")}</DialogDescription>
          </DialogHeader>
          <ul className="border rounded-md">
            {open?.clients.map((c) => (
              <li key={c.client} className="num flex items-center justify-between gap-3 px-3 py-2 text-xs">
                <span>{c.client}</span>
                {c.locked && <Badge variant="secondary">{t("home.holdsLock")}</Badge>}
              </li>
            ))}
          </ul>
          <DialogFooter>
            <Button variant="outline" size="sm" onClick={() => setPendingSwitch(null)}>
              {t("common.cancel")}
            </Button>
            <Button
              size="sm"
              onClick={() => {
                if (pendingSwitch) startBench(pendingSwitch, true);
                setPendingSwitch(null);
              }}
            >
              {t("home.switchConfirm")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function Stat({ value, label }: { value: number | undefined; label: string }) {
  return (
    <div className="text-right">
      <dd className="num text-xl leading-none font-semibold">{value ?? "–"}</dd>
      <dt className="text-muted-foreground mt-1 text-[11px]">{label}</dt>
    </div>
  );
}

function SectionTitle({ id, title, count }: { id: string; title: string; count?: number }) {
  return (
    <h2 id={id} className="mb-3 flex items-baseline gap-2 text-sm font-semibold">
      {title}
      {count !== undefined && <span className="text-muted-foreground num text-xs font-normal">{count}</span>}
    </h2>
  );
}

/** One bench file. The open bench is marked, spans the row and carries the one primary action (#281). */
function BenchCard({
  bench: b,
  session,
  busy,
  onOpen,
}: {
  bench: BenchFile;
  session: SessionInfo | undefined;
  busy: boolean;
  onOpen: () => void;
}) {
  const { t } = useTranslation();
  return (
    <li
      className={cn(
        "bg-card group flex min-w-0 flex-col rounded-xl border p-4 transition-[border-color,box-shadow]",
        session ? "border-primary/50 ring-primary/15 shadow-sm ring-4 sm:col-span-2" : "hover:border-foreground/20 hover:shadow-sm",
        b.error && "border-error/40",
      )}
    >
      <div className="flex items-start gap-3">
        <span
          className={cn(
            "grid size-9 shrink-0 place-items-center rounded-lg",
            b.error ? "bg-error/10 text-error" : session ? "bg-primary text-primary-foreground" : "bg-primary/10 text-primary",
          )}
        >
          {b.error ? <FileWarning className="size-4.5" /> : <Workflow className="size-4.5" />}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate font-semibold">{b.name}</span>
            {session && (
              <span className="text-ok inline-flex shrink-0 items-center gap-1 text-[11px] font-medium">
                <span className="bg-ok size-1.5 rounded-full" aria-hidden />
                {t("home.openNow")}
              </span>
            )}
          </div>
          {!b.error && <div className="text-muted-foreground text-xs">{t("home.instruments", { count: b.instruments })}</div>}
        </div>
      </div>
      <p className={cn("mt-3 line-clamp-2 min-h-[2lh] flex-1 text-[12.5px] leading-normal", b.error ? "text-error" : "text-muted-foreground")}>
        {b.error ? `${t("home.brokenFile")}: ${b.error}` : b.description || " "}
      </p>
      <div className="mt-3 flex items-center gap-3 border-t pt-3">
        {session ? (
          <ScriptAccess session={session} />
        ) : (
          <span className="text-muted-foreground num min-w-0 flex-1 truncate text-[11px]" title={b.path}>
            {b.path}
          </span>
        )}
        <Button
          size="sm"
          className="ml-auto shrink-0"
          variant={session ? "default" : "outline"}
          disabled={!!b.error || busy}
          onClick={onOpen}
        >
          {session ? t("home.continue") : t("home.open")}
          {session && <ArrowRight className="size-3.5" />}
        </Button>
      </div>
    </li>
  );
}

/** A faint scope trace behind the page title: CH1 and CH2 on the graticule (decoration only). */
function HeroTrace() {
  return (
    <svg
      viewBox="0 0 600 160"
      preserveAspectRatio="none"
      aria-hidden
      className="hero-trace pointer-events-none absolute inset-y-0 right-0 hidden h-full w-[60%] opacity-50 md:block"
    >
      <path d="M0 110 C 50 110 60 40 110 40 S 170 120 220 120 S 280 30 330 30 S 390 110 440 110 S 500 50 550 50 S 590 80 600 80" fill="none" stroke="var(--ch1)" strokeWidth="1.5" strokeOpacity="0.55" />
      <path d="M0 70 L 60 70 L 60 130 L 150 130 L 150 70 L 240 70 L 240 130 L 330 130 L 330 70 L 420 70 L 420 130 L 510 130 L 510 70 L 600 70" fill="none" stroke="var(--ch2)" strokeWidth="1.25" strokeOpacity="0.35" />
    </svg>
  );
}

/** Marks the open bench and shows the addresses scripts connect to (issue 31). */
function ScriptAccess({ session }: { session: SessionInfo }) {
  const { t } = useTranslation();
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          className="text-primary inline-flex items-center gap-1.5 text-xs font-medium"
          data-testid="script-access"
        >
          <Plug className="size-3.5" /> {t("home.scriptAccess")}
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-96">
        <p className="text-muted-foreground mb-2 text-xs">{t("home.addresses")}</p>
        <ul className="space-y-1.5">
          {session.instruments.map((i) => (
            <li key={i.name} className="text-xs">
              <span className="font-medium">{i.name}</span>
              <div className="num text-muted-foreground truncate" title={i.address_virtual}>
                {i.address_virtual}
              </div>
              {i.address_hislip && (
                <div className="num text-muted-foreground truncate" title={i.address_hislip}>
                  {i.address_hislip}
                </div>
              )}
              {i.address_vxi11 && (
                <div className="num text-muted-foreground truncate" title={i.address_vxi11}>
                  {i.address_vxi11}
                </div>
              )}
            </li>
          ))}
        </ul>
        <p className="text-muted-foreground mt-2 text-[11px]">
          {t("home.clockOpen")} · t = <span className="num">{formatClock(session.now_ns)}</span>
        </p>
      </PopoverContent>
    </Popover>
  );
}
