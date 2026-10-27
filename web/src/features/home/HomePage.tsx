import { FileWarning, FolderOpen, Play, Search, Workflow } from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import type { BenchFile, SessionInfo } from "@/api/client";
import { useBenches, useSessions, useStartSession } from "@/api/hooks";
import { Logo } from "@/components/app/Logo";
import { PreferencesMenu } from "@/components/app/PreferencesMenu";
import { EmptyState, ErrorState } from "@/components/app/StateViews";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { formatClock } from "@/lib/format";
import { NewBenchDialog } from "./NewBenchDialog";

export function HomePage() {
  const { t } = useTranslation();
  const benches = useBenches();
  const sessions = useSessions();
  const start = useStartSession();
  const navigate = useNavigate();
  const [filter, setFilter] = useState("");

  const files = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return (benches.data ?? []).filter((b) => !q || b.name.toLowerCase().includes(q) || b.path.toLowerCase().includes(q));
  }, [benches.data, filter]);

  const onStart = (b: BenchFile) =>
    start.mutate(b.path, {
      onSuccess: (s) => navigate(`/s/${s.id}`),
      onError: (e) => toast.error(t("errors.generic"), { description: (e as Error).message }),
    });

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
            {(sessions.data?.length ?? 0) > 0 && (
              <section className="mt-8">
                <h2 className="text-muted-foreground mb-2 text-xs font-medium tracking-wide uppercase">{t("home.running")}</h2>
                <div className="grid gap-2">
                  {sessions.data!.map((s) => (
                    <RunningCard key={s.id} s={s} onOpen={() => navigate(`/s/${s.id}`)} />
                  ))}
                </div>
              </section>
            )}

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
                <ul className="divide-y overflow-hidden rounded-lg border">
                  {files.map((b) => (
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
                      <span className="num text-muted-foreground hidden text-xs md:block">{b.path}</span>
                      <Button size="sm" onClick={() => onStart(b)} disabled={!!b.error || start.isPending}>
                        <Play className="size-3.5" /> {t("home.start")}
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </>
        )}
      </main>
    </div>
  );
}

function RunningCard({ s, onOpen }: { s: SessionInfo; onOpen: () => void }) {
  const { t } = useTranslation();
  return (
    <button
      onClick={onOpen}
      className="bg-card hover:bg-accent/50 focus-visible:ring-ring flex items-center gap-4 rounded-lg border px-4 py-3 text-left transition-colors focus-visible:ring-2 focus-visible:outline-none"
    >
      <span className="bg-ok size-2 shrink-0 rounded-full" aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="font-medium">{s.bench_name}</div>
        <div className="text-muted-foreground text-xs">
          {t(`workbench.clockMode.${s.clock_mode}`)} · <span className="num">{formatClock(s.now_ns)}</span> ·{" "}
          {t("home.instruments", { count: s.instruments.length })}
        </div>
      </div>
      <span className="text-primary text-xs font-medium">{t("home.continue")} →</span>
    </button>
  );
}
