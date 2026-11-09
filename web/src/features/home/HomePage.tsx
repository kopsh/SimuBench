import { ArrowRight, FileWarning, Play, Plug, Plus, Search, SearchX, Workflow } from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import type { BenchFile, SessionInfo } from "@/api/client";
import { useBenches, useCatalog, useCreateHello, useRuns, useSessions, useStartSession } from "@/api/hooks";
import { isCancelled } from "@/components/app/ClientsConfirm";
import { Logo } from "@/components/app/Logo";
import { PreferencesMenu } from "@/components/app/PreferencesMenu";
import { EmptyState, ErrorState } from "@/components/app/StateViews";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { formatClock } from "@/lib/format";
import { cn } from "@/lib/utils";
import { RecentRuns } from "../runs/RecentRuns";
import { HelloPreview } from "./HelloPreview";
import { InstrumentLibrary } from "./InstrumentLibrary";
import { filterBenches } from "./model";
import { NewBenchDialog } from "./NewBenchDialog";
import { Showcase } from "./Showcase";

/** The packaged first bench, copied into the workspace on the first click (POST /api/benches/hello). */
const HELLO_PATH = "hello.bench.yaml";

/**
 * The home page introduces the product (docs/ui-design.md 2): what SimuBench is, the Hello World
 * bench playing live, the same bench in the web, the terminal and a script, the instrument library.
 * The user's own benches come first once there are any, with the open one to resume (one bench is
 * open at a time, issue 31).
 */
export function HomePage() {
  const { t } = useTranslation();
  const benches = useBenches();
  const catalog = useCatalog();
  const sessions = useSessions();
  const start = useStartSession();
  const createHello = useCreateHello();
  const hasRuns = (useRuns().data ?? []).length > 0;
  const navigate = useNavigate();
  const [benchFilter, setBenchFilter] = useState("");
  const [libraryFilter, setLibraryFilter] = useState("");
  const [pendingSwitch, setPendingSwitch] = useState<BenchFile | null>(null);

  const open = (sessions.data ?? [])[0];
  const all = benches.data ?? [];
  const files = useMemo(() => filterBenches(benches.data ?? [], benchFilter, open?.bench_path), [benches.data, benchFilter, open?.bench_path]);
  const hello = all.find((b) => b.path === HELLO_PATH);
  const busy = start.isPending || createHello.isPending;

  const openBench = (b: BenchFile) => {
    if (open && open.bench_path !== b.path && open.clients.length > 0) {
      setPendingSwitch(b); // scripts are connected: ask before cutting the ports
      return;
    }
    startBench(b.path);
  };

  // ``force`` once the user confirmed dropping the scripts below; otherwise the engine asks (#121).
  const startBench = (path: string, force = false) =>
    start.mutate(
      { bench_path: path, force },
      {
        onSuccess: (s) => navigate(`/s/${s.id}`),
        onError: (e) => !isCancelled(e) && toast.error(t("errors.generic"), { description: (e as Error).message }),
      },
    );

  const startHello = () => {
    if (open?.bench_path === HELLO_PATH) return navigate(`/s/${open.id}`);
    if (hello) return openBench(hello);
    createHello.mutate(undefined, {
      onSuccess: (doc) => {
        toast.success(t("home.hello.created", { path: doc.path }));
        startBench(doc.path);
      },
      onError: (e) => toast.error(t("errors.generic"), { description: (e as Error).message }),
    });
  };

  const error = benches.error ?? sessions.error;

  return (
    <div className="flex h-full flex-col">
      <header className="bg-background/80 relative z-10 flex h-11 shrink-0 items-center gap-3 border-b px-4 backdrop-blur">
        <Logo />
        <span className="text-muted-foreground hidden text-xs sm:inline">{t("app.tagline")}</span>
        <span className="text-muted-foreground bg-card ml-auto hidden items-center gap-2 rounded-full border px-2.5 py-0.5 text-[11.5px] md:inline-flex">
          <span className="bg-ok size-1.5 rounded-full" aria-hidden />
          {t("home.engine")} <span className="num">{window.location.host}</span>
        </span>
        <div className="md:ml-0 ml-auto">
          <PreferencesMenu />
        </div>
      </header>

      <div className="flex-1 overflow-auto">
        <section className="home-hero relative">
          <div className="relative mx-auto w-full max-w-6xl px-4 sm:px-8">
            {open && <ResumeStrip session={open} onEnter={() => navigate(`/s/${open.id}`)} />}
            <div className="grid items-center gap-10 pt-10 pb-14 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)] lg:pt-14">
              <div className="home-rise">
                <span className="text-primary num inline-flex items-center gap-2 text-[11px] tracking-[0.08em] uppercase">
                  <i className="h-px w-4 bg-current" aria-hidden />
                  Virtual Instrument Bench
                </span>
                <h1 className="mt-3 text-3xl leading-tight font-bold tracking-tight sm:text-[40px]">
                  {t("home.hero.line1")}
                  <br />
                  {t("home.hero.line2a")}
                  <em className="home-accent not-italic">{t("home.hero.accent")}</em>
                  {t("home.hero.line2b")}
                </h1>
                <p className="text-muted-foreground mt-4 max-w-[32em] text-[14.5px]">{t("home.hero.lede")}</p>
                <div className="mt-7 flex flex-wrap items-center gap-2.5">
                  <Button size="lg" className="home-cta h-10 px-5" disabled={busy} onClick={startHello}>
                    <Play className="size-4" />
                    {hello ? t("home.hello.open") : t("home.hello.start")}
                  </Button>
                  <NewBenchDialog
                    trigger={
                      <Button size="lg" variant="outline" className="h-10 px-5">
                        <Plus className="size-4" /> {t("home.hero.blank")}
                      </Button>
                    }
                  />
                </div>
                <p className="text-muted-foreground mt-3 text-[11.5px]">
                  {hello ? t("home.hello.exists", { path: HELLO_PATH }) : t("home.hello.note", { path: HELLO_PATH })}
                </p>
              </div>
              <div className="home-rise [animation-delay:120ms]">
                <HelloPreview />
              </div>
            </div>
          </div>
        </section>

        <main className="mx-auto w-full max-w-6xl space-y-16 px-4 pb-16 sm:px-8">
          {error ? (
            <ErrorState
              error={error}
              onRetry={() => {
                void benches.refetch();
                void sessions.refetch();
              }}
            />
          ) : (
            all.length > 0 && (
              <section aria-labelledby="home-benches" className={cn("grid gap-8", hasRuns && "lg:grid-cols-[minmax(0,1fr)_340px]")}>
                <div className="min-w-0">
                  <div className="mb-3 flex flex-wrap items-center gap-2">
                    <h2 id="home-benches" className="text-base font-semibold">
                      {t("home.files")} <span className="text-muted-foreground num text-xs font-normal">{all.length}</span>
                    </h2>
                    <div className="relative ml-auto w-56">
                      <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2" />
                      <Input
                        value={benchFilter}
                        onChange={(e) => setBenchFilter(e.target.value)}
                        onKeyDown={(e) => e.key === "Escape" && setBenchFilter("")}
                        placeholder={t("home.filter")}
                        className="h-8 pl-8"
                        aria-label={t("home.filter")}
                      />
                    </div>
                    <NewBenchDialog />
                  </div>
                  {files.length === 0 ? (
                    <div className="rounded-xl border border-dashed">
                      <EmptyState
                        icon={<SearchX className="size-6" />}
                        title={t("home.noMatch", { query: benchFilter.trim() })}
                        action={
                          <Button variant="outline" size="sm" onClick={() => setBenchFilter("")}>
                            {t("home.clearFilter")}
                          </Button>
                        }
                      />
                    </div>
                  ) : (
                    <ul className="bg-card divide-y overflow-hidden rounded-xl border" data-testid="bench-list">
                      {files.map((b) => (
                        <BenchRow
                          key={b.path}
                          bench={b}
                          session={open && open.bench_path === b.path ? open : undefined}
                          busy={busy}
                          onOpen={(s) => (s ? navigate(`/s/${s.id}`) : openBench(b))}
                        />
                      ))}
                    </ul>
                  )}
                </div>
                {hasRuns && (
                  <div className="min-w-0 lg:-mt-8">
                    <RecentRuns />
                  </div>
                )}
              </section>
            )
          )}

          <section aria-labelledby="home-show">
            <SectionHead id="home-show" kicker="One bench · every interface" title={t("home.show.title")} body={t("home.show.body")} />
            <Showcase />
          </section>

          <section aria-labelledby="home-library">
            <div className="flex flex-wrap items-end gap-4">
              <SectionHead id="home-library" kicker="Instrument library" title={t("home.library.title")} body={t("home.library.body")} />
              <div className="relative mb-5 ml-auto w-full sm:w-72">
                <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2" />
                <Input
                  value={libraryFilter}
                  onChange={(e) => setLibraryFilter(e.target.value)}
                  onKeyDown={(e) => e.key === "Escape" && setLibraryFilter("")}
                  placeholder={t("home.library.search")}
                  className="h-9 pl-8"
                  aria-label={t("home.library.search")}
                />
              </div>
            </div>
            <InstrumentLibrary catalog={catalog.data} loading={catalog.isLoading} query={libraryFilter} />
          </section>

          {all.length === 0 && !benches.isLoading && !error && (
            <section className="bg-card relative overflow-hidden rounded-2xl border px-6 py-10 text-center">
              <div className="home-glow pointer-events-none absolute inset-0" aria-hidden />
              <h2 className="relative text-2xl font-bold">{t("home.final.title")}</h2>
              <p className="text-muted-foreground relative mt-1.5">{t("home.final.body")}</p>
              <Button size="lg" className="home-cta relative mt-6 h-10 px-5" disabled={busy} onClick={startHello}>
                <Play className="size-4" /> {t("home.hello.start")}
              </Button>
            </section>
          )}
        </main>
      </div>

      <Dialog open={!!pendingSwitch} onOpenChange={(v) => !v && setPendingSwitch(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("home.switchTitle")}</DialogTitle>
            <DialogDescription>{t("home.switchBody")}</DialogDescription>
          </DialogHeader>
          <ul className="rounded-md border">
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
                if (pendingSwitch) startBench(pendingSwitch.path, true);
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

function SectionHead({ id, kicker, title, body }: { id: string; kicker: string; title: string; body: string }) {
  return (
    <div className="mb-5 max-w-3xl">
      <div className="text-primary num text-[11px] tracking-[0.08em] uppercase">{kicker}</div>
      <h2 id={id} className="mt-1.5 text-2xl font-bold tracking-tight">
        {title}
      </h2>
      <p className="text-muted-foreground mt-1.5 text-sm">{body}</p>
    </div>
  );
}

/** The open bench, to go back to (returning visits). */
function ResumeStrip({ session, onEnter }: { session: SessionInfo; onEnter: () => void }) {
  const { t } = useTranslation();
  return (
    <div className="border-primary/40 bg-primary/10 mt-5 flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border px-4 py-2.5">
      <span className="bg-ok size-2 rounded-full" aria-hidden />
      <span className="text-sm">
        <b className="font-semibold">{session.bench_name}</b> {t("home.openNow")}
      </span>
      <span className="text-muted-foreground num text-xs">
        {t("home.instruments", { count: session.instruments.length })} · {t("home.clients", { count: session.clients.length })} · t ={" "}
        {formatClock(session.now_ns)}
      </span>
      <Button size="sm" className="ml-auto" onClick={onEnter}>
        {t("home.continue")} <ArrowRight className="size-3.5" />
      </Button>
    </div>
  );
}

/** One of the user's bench files; the open one carries the one primary action (#281). */
function BenchRow({ bench: b, session, busy, onOpen }: { bench: BenchFile; session: SessionInfo | undefined; busy: boolean; onOpen: (s?: SessionInfo) => void }) {
  const { t } = useTranslation();
  return (
    <li className={cn("flex items-center gap-3 px-4 py-2.5 transition-colors", session ? "bg-primary/5" : "hover:bg-accent/40")}>
      <span className={cn("grid size-8 shrink-0 place-items-center rounded-lg", b.error ? "bg-error/10 text-error" : "bg-primary/10 text-primary")}>
        {b.error ? <FileWarning className="size-4" /> : <Workflow className="size-4" />}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate font-medium">{b.name}</span>
          {session && (
            <span className="text-ok inline-flex shrink-0 items-center gap-1 text-[11px] font-medium">
              <span className="bg-ok size-1.5 rounded-full" aria-hidden />
              {t("home.openNow")}
            </span>
          )}
          {!b.error && <span className="text-muted-foreground shrink-0 text-xs">{t("home.instruments", { count: b.instruments })}</span>}
        </div>
        <div className={cn("truncate text-xs", b.error ? "text-error" : "text-muted-foreground")}>
          {b.error ? `${t("home.brokenFile")}: ${b.error}` : b.description || <span className="num">{b.path}</span>}
        </div>
      </div>
      {session ? <ScriptAccess session={session} /> : <span className="text-muted-foreground num hidden max-w-56 truncate text-[11px] xl:block">{b.path}</span>}
      <Button size="sm" variant={session ? "default" : "outline"} disabled={!!b.error || busy} onClick={() => onOpen(session)}>
        {session ? t("home.continue") : t("home.open")}
      </Button>
    </li>
  );
}

/** Marks the open bench and shows the addresses scripts connect to (issue 31). */
function ScriptAccess({ session }: { session: SessionInfo }) {
  const { t } = useTranslation();
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button className="text-primary inline-flex shrink-0 items-center gap-1.5 text-xs font-medium" data-testid="script-access">
          <Plug className="size-3.5" /> {t("home.scriptAccess")}
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-96">
        <p className="text-muted-foreground mb-2 text-xs">{t("home.addresses")}</p>
        <ul className="space-y-1.5">
          {session.instruments.map((i) => (
            <li key={i.name} className="text-xs">
              <span className="font-medium">{i.name}</span>
              {[i.address_virtual, i.address_hislip, i.address_vxi11].filter(Boolean).map((a) => (
                <div key={a} className="num text-muted-foreground truncate" title={a ?? ""}>
                  {a}
                </div>
              ))}
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
