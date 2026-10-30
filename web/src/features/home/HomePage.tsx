import { FileWarning, FolderOpen, Plug, Search, Workflow } from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import type { BenchFile, SessionInfo } from "@/api/client";
import { useBenches, useSessions, useStartSession } from "@/api/hooks";
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
import { RecentRuns } from "../runs/RecentRuns";
import { GettingStarted } from "./GettingStarted";
import { NewBenchDialog } from "./NewBenchDialog";

export function HomePage() {
  const { t } = useTranslation();
  const benches = useBenches();
  const sessions = useSessions();
  const start = useStartSession();
  const navigate = useNavigate();
  const [filter, setFilter] = useState("");
  const [pendingSwitch, setPendingSwitch] = useState<BenchFile | null>(null);

  const files = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return (benches.data ?? []).filter((b) => !q || b.name.toLowerCase().includes(q) || b.path.toLowerCase().includes(q));
  }, [benches.data, filter]);

  // One bench is open at a time (it holds the SCPI ports); a bench opens on click (issue 31).
  const open = (sessions.data ?? [])[0];

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
        <span className="text-muted-foreground text-xs">{t("app.tagline")}</span>
        <div className="ml-auto">
          <PreferencesMenu />
        </div>
      </header>
      <main className="mx-auto w-full max-w-5xl flex-1 overflow-auto px-6 py-8">
        <h1 className="text-lg font-semibold">{t("home.title")}</h1>
        <p className="text-muted-foreground mt-1">{t("home.subtitle")}</p>
        <GettingStarted example={(benches.data ?? []).find((b) => !b.error && b.instruments > 0)} onStart={openBench} />

        {error ? (
          <ErrorState
            error={error}
            onRetry={() => {
              void benches.refetch();
              void sessions.refetch();
            }}
          />
        ) : (
          <>
            <section className="mt-8">
              <div className="mb-2 flex items-center gap-3">
                <h2 className="text-muted-foreground text-xs font-medium tracking-wide uppercase">{t("home.files")}</h2>
                <div className="ml-auto">
                  <NewBenchDialog />
                </div>
                <div className="relative w-64">
                  <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2" />
                  <Input
                    value={filter}
                    onChange={(e) => setFilter(e.target.value)}
                    placeholder={t("home.filter")}
                    className="h-8 pl-8"
                    aria-label={t("home.filter")}
                  />
                </div>
              </div>
              {benches.isLoading ? (
                <div className="grid gap-2">
                  {[0, 1, 2].map((i) => (
                    <Skeleton key={i} className="h-16 w-full" />
                  ))}
                </div>
              ) : files.length === 0 ? (
                <EmptyState icon={<FolderOpen className="size-6" />} title={t("home.emptyTitle")} body={t("home.emptyBody")} />
              ) : (
                <ul className="divide-y overflow-hidden rounded-lg border" data-testid="bench-list">
                  {files.map((b) => {
                    const session = open && open.bench_path === b.path ? open : undefined;
                    return (
                      <li key={b.path} className="bg-card hover:bg-accent/50 flex items-center gap-4 px-4 py-3 transition-colors">
                        <Workflow className="text-muted-foreground size-5 shrink-0" />
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <span className="truncate font-medium">{b.name}</span>
                            {!b.error && (
                              <Badge variant="secondary" className="font-normal">
                                {t("home.instruments", { count: b.instruments })}
                              </Badge>
                            )}
                          </div>
                          <div className="text-muted-foreground truncate text-xs">
                            {b.error ? (
                              <span className="text-error inline-flex items-center gap-1">
                                <FileWarning className="size-3.5" />
                                {t("home.brokenFile")}: {b.error}
                              </span>
                            ) : (
                              b.description || <span className="num">{b.path}</span>
                            )}
                          </div>
                        </div>
                        {session ? (
                          <ScriptAccess session={session} />
                        ) : (
                          <span className="text-muted-foreground num hidden text-xs md:block">{b.path}</span>
                        )}
                        <Button
                          size="sm"
                          variant={session ? "secondary" : "default"}
                          disabled={!!b.error || start.isPending}
                          onClick={() => (session ? navigate(`/s/${session.id}`) : openBench(b))}
                        >
                          {session ? t("home.continue") : t("home.open")}
                        </Button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>
            <RecentRuns />
          </>
        )}
      </main>

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
