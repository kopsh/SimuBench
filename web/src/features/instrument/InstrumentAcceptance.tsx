import { useQueryClient } from "@tanstack/react-query";
import { Blocks, FlaskConical, History, Info, ListTree, Terminal, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  keys,
  useCloseTry,
  useDefinition,
  useDefinitionCoverage,
  useDefinitionDocument,
  useOpenTry,
  useTryInstrument,
  useTryInternals,
} from "@/api/hooks";
import { ErrorState } from "@/components/app/StateViews";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";
import { InternalsPanel } from "@/features/workbench/internals/InternalsView";
import { CasesTab } from "./CasesTab";
import { CommandsTab } from "./CommandsTab";
import { HistoryTab } from "./HistoryTab";
import { OverviewTab } from "./OverviewTab";
import { TryTab } from "./TryTab";

const SECTIONS = [
  { key: "overview", icon: Info },
  { key: "commands", icon: ListTree },
  { key: "internals", icon: Blocks },
  { key: "try", icon: Terminal },
  { key: "cases", icon: FlaskConical },
  { key: "history", icon: History },
] as const;

type SectionKey = (typeof SECTIONS)[number]["key"];

/**
 * The acceptance page of an instrument definition (T4, instrument-authoring.md 7.2): read-only, in
 * the instrument's language. Opened as a workbench tab beside the internals view, or on its own
 * page from the home screen's instrument list.
 */
export function InstrumentAcceptance({ definitionId }: { definitionId: string }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const doc = useDefinitionDocument(definitionId);
  const sha = doc.data?.sha256;
  const definition = useDefinition(definitionId, sha ?? "");
  const coverage = useDefinitionCoverage(definitionId, sha ?? "");
  const [section, setSection] = useState<SectionKey>("overview");
  const trySession = useTrySession(definitionId, sha);
  const inst = useTryInstrument(trySession.sid);
  const [notice, setNotice] = useState(false);
  const seen = useRef<string | null>(null);

  useEffect(() => {
    if (!sha) return;
    if (seen.current && seen.current !== sha) {
      setNotice(true);
      // What the page shows follows the new file, not just the hash.
      void qc.invalidateQueries({ queryKey: keys.definitionCases(definitionId) });
    }
    seen.current = sha;
  }, [sha, definitionId, qc]);

  if (doc.isLoading) {
    return (
      <div className="space-y-3 p-4">
        <Skeleton className="h-10 w-96" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }
  if (doc.error || !doc.data) {
    return <ErrorState error={doc.error ?? new Error(t("acceptance.notFound", { id: definitionId }))} onRetry={() => void doc.refetch()} />;
  }
  const d = definition.data;
  const reference = d?.reference;
  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b px-4 py-2">
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-2">
          <h1 className="text-lg font-semibold">{inst.data?.model ?? d?.model ?? definitionId}</h1>
          <span className="text-muted-foreground text-xs">{inst.data?.manufacturer ?? d?.manufacturer}</span>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {d && (
            <Badge variant="secondary" className="font-normal">
              {t(`acceptance.class.${d.class}`, { defaultValue: d.class })}
            </Badge>
          )}
          <Badge variant="outline" className={cn("font-normal", reference && "border-sim/40 text-sim")}>
            {reference ? t("acceptance.realModel") : t("acceptance.customModel")}
          </Badge>
          <Badge variant="outline" className="font-normal">
            {doc.data.editable ? t("acceptance.workspace") : t("acceptance.builtin")}
          </Badge>
        </div>
        <div className="text-muted-foreground ml-auto flex items-center gap-2 text-[11px]">
          {/* The overview shows the *IDN? reply in full; the header keeps only the copy action (#281). */}
          <button
            type="button"
            title={inst.data?.idn}
            className="hover:text-foreground underline-offset-2 hover:underline"
            onClick={() => void navigator.clipboard?.writeText(inst.data?.idn ?? "")}
            disabled={!inst.data?.idn}
          >
            {t("acceptance.copyIdn")}
          </button>
        </div>
      </header>

      {!doc.data.ok && (
        <div className="bg-error/10 text-error flex items-start gap-2 border-b px-4 py-1.5 text-[11px]" role="alert">
          <span className="shrink-0 font-medium">{t("acceptance.invalid")}</span>
          <ul className="space-y-0.5">
            {doc.data.diagnostics.map((diag, i) => (
              <li key={`${diag.code}-${i}`}>
                {diag.code}: {diag.message}
                {diag.pointer && <span className="num ml-1 opacity-70">{diag.pointer}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
      {notice && (
        <div className="bg-sim-muted text-sim flex items-center gap-2 border-b px-4 py-1.5 text-[11px]" role="status">
          <span>{t("acceptance.reloaded")}</span>
          <button type="button" className="ml-auto rounded p-0.5 hover:opacity-70" aria-label={t("common.close")} onClick={() => setNotice(false)}>
            <X className="size-3.5" />
          </button>
        </div>
      )}

      <Tabs value={section} onValueChange={(v) => setSection(v as SectionKey)} className="min-h-0 flex-1 gap-0">
        <div className="bg-sidebar overflow-x-auto border-b px-3">
          <TabsList variant="line" className="h-10">
            {SECTIONS.map(({ key, icon: Icon }) => (
              <TabsTrigger key={key} value={key} className="text-xs" data-testid={`acceptance-tab-${key}`}>
                <Icon className="size-3.5" /> {t(`acceptance.section.${key}`)}
              </TabsTrigger>
            ))}
          </TabsList>
        </div>
        <TabsContent value="overview" className="min-h-0 flex-1 overflow-auto">
          {d ? <OverviewTab definition={d} inst={inst.data} /> : <DefinitionBody definition={definition} />}
        </TabsContent>
        <TabsContent value="commands" className="min-h-0 flex-1 overflow-hidden">
          {d ? <CommandsTab commands={d.commands ?? []} coverage={coverage.data} /> : <DefinitionBody definition={definition} />}
        </TabsContent>
        <TabsContent value="internals" className="min-h-0 flex-1 overflow-hidden">
          <InternalsSection sid={trySession.sid} inst={inst.data} error={trySession.error} />
        </TabsContent>
        <TabsContent value="try" className="min-h-0 flex-1 overflow-hidden">
          <TryTab sid={trySession.sid} inst={inst.data} error={trySession.error} onReopen={trySession.reopen} />
        </TabsContent>
        <TabsContent value="cases" className="min-h-0 flex-1 overflow-hidden">
          <CasesTab definitionId={definitionId} />
        </TabsContent>
        <TabsContent value="history" className="min-h-0 flex-1 overflow-hidden">
          <HistoryTab definitionId={definitionId} sha={sha} editable={doc.data.editable} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function DefinitionBody({ definition }: { definition: ReturnType<typeof useDefinition> }) {
  if (definition.isLoading) return <Skeleton className="m-4 h-64" />;
  return <ErrorState error={definition.error ?? new Error("definition unavailable")} onRetry={() => void definition.refetch()} />;
}

function InternalsSection({ sid, inst, error }: { sid: string | undefined; inst: ReturnType<typeof useTryInstrument>["data"]; error: string | null }) {
  const view = useTryInternals(sid);
  if (error) return <ErrorState error={new Error(error)} />;
  if (!sid || !inst || !view.data) return <Skeleton className="m-4 h-64" />;
  if (view.error) return <ErrorState error={view.error} onRetry={() => void view.refetch()} />;
  return <InternalsPanel view={view.data} inst={inst} />;
}

/**
 * One temporary session with this definition's instrument: opened when the page opens (and again
 * whenever the definition changes), closed when it closes. The overview, internals and trial run
 * all read this one session, so what the page shows is what the definition builds.
 */
function useTrySession(definitionId: string, sha: string | undefined) {
  const [sid, setSid] = useState<string>();
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);
  const open = useOpenTry();
  const close = useCloseTry();
  const openRef = useRef(open.mutateAsync);
  const closeRef = useRef(close.mutateAsync);
  useEffect(() => {
    openRef.current = open.mutateAsync;
    closeRef.current = close.mutateAsync;
  });

  const reopen = useCallback(async () => {
    const mine = ++generation.current;
    try {
      const opened = await openRef.current(definitionId);
      if (mine !== generation.current) return opened.session_id;
      setSid(opened.session_id);
      setError(null);
      return opened.session_id;
    } catch (err) {
      if (mine !== generation.current) return undefined;
      setSid(undefined);
      setError((err as Error).message);
      return undefined;
    }
  }, [definitionId]);

  useEffect(() => {
    if (sha) void reopen();
  }, [sha, reopen]);

  // Closing the page (or replacing the session) closes the temporary session.
  useEffect(
    () => () => {
      if (sid) void closeRef.current(sid).catch(() => {});
    },
    [sid],
  );
  return { sid, error, reopen };
}
