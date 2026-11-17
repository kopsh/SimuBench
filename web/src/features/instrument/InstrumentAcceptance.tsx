import { useQueryClient } from "@tanstack/react-query";
import { Blocks, Copy, ExternalLink, FlaskConical, FolderOpen, Hammer, History, Info, ListTree, Terminal, X } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { useCatalog, useCopyDefinition } from "@/api/hooks";
import { Button } from "@/components/ui/button";
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
import { BuildTab } from "./structure/BuildTab";
import { CasesTab } from "./CasesTab";
import { CommandsTab } from "./CommandsTab";
import { HistoryTab } from "./HistoryTab";
import { OverviewTab } from "./OverviewTab";
import { TryTab } from "./TryTab";

/** What the page does with a copy of the definition (#1250): `created` is false when an earlier
 * copy is opened instead of a new one made. Without it the page navigates to the copy's own page. */
export type OpenCopy = (definitionId: string, section: SectionKey, created: boolean) => void;

/** The workspace copy already made of `definitionId` ("copy as my instrument" writes one that
 * `extends` it, D59), if any: the first by id. */
export function useCopyOf(definitionId: string): string | null {
  const catalog = useCatalog();
  const mine = (catalog.data?.instruments ?? []).filter((d) => d.workspace && d.extends === definitionId).map((d) => d.id);
  return mine.sort()[0] ?? null;
}

/** "Copy as my instrument" on a built-in model: a workspace definition that extends it (D59), whose
 * YAML the user or the agent changes. When a copy exists the button opens it instead (#1250). */
function CopyAsMine({ definitionId, section, onOpen }: { definitionId: string; section: SectionKey; onOpen?: OpenCopy }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const copy = useCopyDefinition();
  const existing = useCopyOf(definitionId);
  const open = (id: string, created: boolean) => {
    if (onOpen) return onOpen(id, section, created);
    const params = new URLSearchParams(window.location.search);
    params.set("section", section);
    navigate(`/i/${id}?${params.toString()}`);
  };
  if (existing) {
    return (
      <Button size="sm" variant="outline" className="h-7" onClick={() => open(existing, false)} data-testid="open-my-copy">
        <FolderOpen className="size-3.5" /> {t("acceptance.openMyCopy")}
      </Button>
    );
  }
  return (
    <Button
      size="sm"
      variant="outline"
      className="h-7"
      disabled={copy.isPending}
      data-testid="copy-as-mine"
      onClick={() =>
        copy.mutate(definitionId, {
          onSuccess: (doc) => {
            toast.success(t("acceptance.copied", { path: doc.path ?? "" }), { description: t("acceptance.copiedHint") });
            open(doc.data.id as string, true);
          },
          onError: (e) => toast.error(t("errors.generic"), { description: (e as Error).message }),
        })
      }
    >
      <Copy className="size-3.5" /> {t("acceptance.copyAsMine")}
    </Button>
  );
}

const SECTIONS = [
  { key: "overview", icon: Info },
  { key: "cases", icon: FlaskConical },
  { key: "commands", icon: ListTree },
  { key: "internals", icon: Blocks },
  { key: "try", icon: Terminal },
  { key: "build", icon: Hammer },
  { key: "history", icon: History },
] as const;

export type SectionKey = (typeof SECTIONS)[number]["key"];

/** A section key from outside (a tab, a URL); the overview for anything else. */
export function sectionOf(value: string | null | undefined): SectionKey {
  return SECTIONS.find((x) => x.key === value)?.key ?? "overview";
}

/**
 * The acceptance page of an instrument definition (T4, instrument-authoring.md 7.2): read-only, in
 * the instrument's language. Opened as a workbench tab beside the internals view, or on its own
 * page from the home screen's instrument list.
 */
export function InstrumentAcceptance({
  definitionId,
  initialSection,
  onOpenCopy,
  pageHref,
}: {
  definitionId: string;
  /** The section it opens at (#1250): the one a copy was made from. */
  initialSection?: string;
  /** Inside the workbench: show the copy in this tab (#1250); else the page navigates to it. */
  onOpenCopy?: OpenCopy;
  /** Inside the workbench: the definition's own page, which links back to the bench (#1250). */
  pageHref?: string;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const doc = useDefinitionDocument(definitionId);
  const sha = doc.data?.sha256;
  const definition = useDefinition(definitionId, sha ?? "");
  const coverage = useDefinitionCoverage(definitionId, sha ?? "");
  const [section, setSection] = useState<SectionKey>(() => sectionOf(initialSection));
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
          {pageHref && (
            <Link
              to={pageHref}
              className="hover:text-foreground inline-flex items-center gap-1 underline-offset-2 hover:underline"
              data-testid="acceptance-own-page"
            >
              <ExternalLink className="size-3" /> {t("acceptance.ownPage")}
            </Link>
          )}
          {!doc.data.editable && <CopyAsMine definitionId={definitionId} section={section} onOpen={onOpenCopy} />}
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
      {/* The build area saves on every edit and says so itself (#1201): no "reloaded" notice there. */}
      {notice && section !== "build" && (
        <div className="bg-sim-muted text-sim flex items-center gap-2 border-b px-4 py-1.5 text-[11px]" role="status">
          <span>{t("acceptance.reloaded")}</span>
          <button type="button" className="ml-auto rounded p-0.5 hover:opacity-70" aria-label={t("common.close")} onClick={() => setNotice(false)}>
            <X className="size-3.5" />
          </button>
        </div>
      )}

      <Tabs
        value={section}
        onValueChange={(v) => {
          if (section === "build") setNotice(false); // the build area's own saves are not news
          setSection(v as SectionKey);
        }} className="min-h-0 flex-1 gap-0">
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
        <TabsContent value="build" className="min-h-0 flex-1 overflow-hidden">
          {section === "build" && (
            <BuildTab
              definitionId={definitionId}
              tryPanel={<TryTab sid={trySession.sid} inst={inst.data} error={trySession.error} onReopen={trySession.reopen} />}
            />
          )}
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
