import { type QueryClient, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useDeferredValue, useMemo } from "react";
import { useEvents } from "./events";
import i18n from "@/i18n";
import { toast } from "sonner";
import { confirmClients, remoteInstrument } from "@/components/app/ClientsConfirm";
import { stateMap } from "@/features/workbench/model";
import type {
  CaseFile,
  DefinitionDocument,
  CaseRunResult,
  DefinitionChangeList,
  DefinitionCoverage,
  DefinitionVersion,
  InstrumentInfo,
  InstrumentInternals,
  ScpiResponse,
  TryOpened,
} from "./client";
import { ApiError, type FaultSpec, type Schemas, api, unwrap } from "./client";

export const keys = {
  benches: ["benches"] as const,
  sessions: ["sessions"] as const,
  session: (sid: string) => ["session", sid] as const,
  screen: (sid: string, name: string) => ["screen", sid, name] as const,
  catalog: ["catalog"] as const,
  faults: (sid: string) => ["faults", sid] as const,
  readings: (sid: string, name: string) => ["readings", sid, name] as const,
  internals: (sid: string, name: string) => ["internals", sid, name] as const,
  levels: (sid: string) => ["levels", sid] as const,
  benchDoc: (path: string) => ["bench-doc", path] as const,
  preview: (path: string, sha: string) => ["bench-preview", path, sha] as const,
  history: (path: string, sha: string) => ["bench-history", path, sha] as const,
  version: (path: string, seq: number) => ["bench-version", path, seq] as const,
  runs: ["runs"] as const,
  run: (id: string) => ["run", id] as const,
  definition: (id: string) => ["definition", id] as const,
  definitionDoc: (id: string) => ["definition-document", id] as const,
  coverage: (id: string) => ["definition-coverage", id] as const,
  definitionHistory: (id: string) => ["definition-history", id] as const,
  definitionChanges: (id: string, base: number, target: number) => ["definition-changes", id, base, target] as const,
  definitionCases: (id: string) => ["definition-cases", id] as const,
  tryInstrument: (sid: string) => ["try-instrument", sid] as const,
  tryInternals: (sid: string) => ["try-internals", sid] as const,
};

export function useCatalog() {
  return useQuery({ queryKey: keys.catalog, queryFn: () => unwrap(api.GET("/api/catalog")), staleTime: 60_000 });
}

/** The bench file as stored; polled so that changes made elsewhere (an agent, an editor) show up. */
export function useBenchDoc(path: string | null | undefined, pollMs: number | false = 2500) {
  return useQuery({
    queryKey: keys.benchDoc(path ?? ""),
    queryFn: () => unwrap(api.GET("/api/benches/file", { params: { query: { path: path! } } })),
    enabled: !!path,
    refetchInterval: pollMs,
  });
}

/** Saved versions of a bench file, newest first (D35); refetched whenever the file changes. */
export function useBenchHistory(path: string | null | undefined, sha: string | undefined, enabled = true) {
  return useQuery({
    queryKey: keys.history(path ?? "", sha ?? ""),
    queryFn: () => unwrap(api.GET("/api/benches/history", { params: { query: { path: path! } } })),
    enabled: enabled && !!path,
    placeholderData: (prev) => prev,
  });
}

/** One saved version's content; versions never change, so it is cached for good. */
export function useBenchVersion(path: string | null | undefined, seq: number | null | undefined) {
  return useQuery({
    queryKey: keys.version(path ?? "", seq ?? 0),
    queryFn: () => unwrap(api.GET("/api/benches/version", { params: { query: { path: path!, seq: seq! } } })),
    enabled: !!path && !!seq,
    staleTime: Infinity,
  });
}

export function useBenchPreview(path: string | null | undefined, sha: string | undefined) {
  return useQuery({
    queryKey: keys.preview(path ?? "", sha ?? ""),
    queryFn: () => unwrap(api.GET("/api/benches/preview", { params: { query: { path: path! } } })),
    enabled: !!path && !!sha,
    placeholderData: (prev) => prev,
    staleTime: Infinity,
  });
}

export function useBenches() {
  return useQuery({ queryKey: keys.benches, queryFn: () => unwrap(api.GET("/api/benches")) });
}

export function useSessions() {
  // Polled: connected scripts and the "script access" mark on the home page must stay fresh.
  return useQuery({ queryKey: keys.sessions, queryFn: () => unwrap(api.GET("/api/sessions")), refetchInterval: 2000 });
}

export function useSession(sid: string) {
  return useQuery({
    queryKey: keys.session(sid),
    queryFn: () => unwrap(api.GET("/api/sessions/{sid}", { params: { path: { sid } } })),
    retry: (count, err) => (err as { status?: number }).status !== 404 && count < 3,
    // Scripts connect and leave without an event: keep the remote marks fresh (#121).
    refetchInterval: (q) => (q.state.data?.clients.length ? 2000 : 5000),
  });
}

/**
 * Start a bench (stopping the open one). With scripts connected the user confirms first (#121);
 * ``rebuild`` asks as a rebuild (with "later"), ``force`` skips the question (already asked).
 */
/** Copy the packaged Hello World bench into the workspace (kept as is when it is already there). */
export function useCreateHello() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/benches/hello")),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.benches }),
  });
}

/** "Copy as my instrument": a workspace definition extending a built-in one (D59). */
export function useCopyDefinition() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (defId: string) => unwrap(api.POST("/api/definitions/{def_id}/copy", { params: { path: { def_id: defId } } })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.catalog }),
  });
}

export function useStartSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (arg: string | { bench_path: string; rebuild?: boolean; force?: boolean }) => {
      const { bench_path, rebuild = false, force: asked = false } = typeof arg === "string" ? { bench_path: arg } : arg;
      return confirmClients(rebuild ? "rebuild" : "start", (force) =>
        unwrap(api.POST("/api/sessions", { body: { bench_path, force: force || asked } })),
      );
    },
    onSuccess: (s) => {
      qc.setQueryData(keys.session(s.id), s);
      void qc.invalidateQueries({ queryKey: keys.sessions });
    },
  });
}

export function useStopSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (sid: string) =>
      confirmClients("stop", (force) => unwrap(api.DELETE("/api/sessions/{sid}", { params: { path: { sid }, query: { force } } }))),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.sessions }),
  });
}

/** Power-cycle: fresh instruments, a new record, the same session id (issue 31). */
export function useResetSession(sid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      confirmClients("reset", (force) => unwrap(api.POST("/api/sessions/{sid}/reset", { params: { path: { sid } }, body: { force } }))),
    onSuccess: (s) => {
      qc.setQueryData(keys.session(s.id), s);
      void qc.invalidateQueries({ queryKey: keys.sessions });
    },
  });
}

/** LOCAL: take an instrument in remote control back to the UI, until the script's next command (#121). */
export function useGoLocal(sid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => goLocal(qc, sid, name),
  });
}

async function goLocal(qc: QueryClient, sid: string, name: string) {
  const inst = await unwrap(api.POST("/api/sessions/{sid}/instruments/{name}/local", { params: { path: { sid, name } } }));
  await qc.invalidateQueries({ queryKey: keys.session(sid) });
  return inst;
}

/** A UI operation refused because a script holds the instrument: say so and offer LOCAL (#121). */
function remoteRefused(qc: QueryClient, sid: string) {
  return (e: unknown) => {
    const name = remoteInstrument(e);
    if (!name) return;
    void qc.invalidateQueries({ queryKey: keys.session(sid) });
    toast.warning(i18n.t("remote.refused", { name }), {
      id: `remote-${name}`,
      description: i18n.t("remote.refusedHint"),
      action: {
        label: i18n.t("remote.local"),
        onClick: () => void goLocal(qc, sid, name).catch((err: Error) => toast.error(err.message)),
      },
    });
  };
}

/** What one SCPI exchange means for the user (issue 639): ok, or the one message to show —
 * the link's error first (the exchange never happened), then the first queued error. Every
 * send path reports through this, so a missing branch is one fix, not one per screen. */
export function scpiOutcome(r: ScpiResponse): { ok: boolean; message: string | null } {
  if (r.link_error) return { ok: false, message: r.link_error };
  const first = r.errors[0];
  if (first) return { ok: false, message: `${first.code}, ${first.message}` };
  return { ok: true, message: null };
}

export function useScpi(sid: string, name: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (command: string) =>
      unwrap(api.POST("/api/sessions/{sid}/instruments/{name}/scpi", { params: { path: { sid, name } }, body: { command } })),
    onError: remoteRefused(qc, sid),
  });
}

export function useConfigure(sid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ instrument, path, value }: { instrument: string; path: string; value: unknown }) =>
      unwrap(
        api.POST("/api/sessions/{sid}/instruments/{name}/configure", {
          params: { path: { sid, name: instrument } },
          body: { path, value },
        }),
      ),
    onError: remoteRefused(qc, sid),
  });
}

export function useAdvance(sid: string) {
  return useMutation({
    mutationFn: (seconds: number) =>
      confirmClients("advance", (force) =>
        unwrap(api.POST("/api/sessions/{sid}/clock/advance", { params: { path: { sid } }, body: { seconds, force } })),
      ),
  });
}

/** Every net's level and a thumbnail, re-read when the bench changes (D44). */
export function useNetLevels(sid: string, version: number, enabled = true) {
  return useQuery({
    queryKey: [...keys.levels(sid), version],
    queryFn: () => unwrap(api.GET("/api/sessions/{sid}/levels", { params: { path: { sid } } })),
    enabled,
    placeholderData: (prev) => prev,
    staleTime: Infinity,
  });
}

/** How often a running scope's screen redraws, in real time (reads only: no SCPI, no virtual time). */
const SCREEN_REFRESH_MS = 300;

export function useScopeScreen(sid: string, name: string, version: number) {
  return useQuery({
    queryKey: [...keys.screen(sid, name), version],
    queryFn: () =>
      unwrap(api.GET("/api/sessions/{sid}/instruments/{name}/screen", { params: { path: { sid, name } } })),
    placeholderData: (prev) => prev,
    staleTime: Infinity,
    refetchInterval: (q) => (q.state.data?.running ? SCREEN_REFRESH_MS : false),
  });
}

/** A front-panel button (Run, Stop, Single): inside the instrument, not a SCPI command (D32). */
export function usePanel(sid: string, name: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (action: "run" | "stop" | "single") =>
      unwrap(api.POST("/api/sessions/{sid}/instruments/{name}/panel", { params: { path: { sid, name } }, body: { action } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.screen(sid, name) }),
    onError: remoteRefused(qc, sid),
  });
}

export function useFaults(sid: string) {
  return useQuery({
    queryKey: keys.faults(sid),
    queryFn: () => unwrap(api.GET("/api/sessions/{sid}/faults", { params: { path: { sid } } })),
  });
}

export function useInjectFault(sid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (fault: FaultSpec) => unwrap(api.POST("/api/sessions/{sid}/faults", { params: { path: { sid } }, body: fault })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.faults(sid) }),
  });
}

export function useClearFault(sid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (fault_id: string) =>
      unwrap(api.DELETE("/api/sessions/{sid}/faults/{fault_id}", { params: { path: { sid, fault_id } } })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.faults(sid) }),
  });
}

/** An instrument's components and internal wiring (issue 20); bound values follow its settings. */
export function useInternals(sid: string, name: string, version: number) {
  return useQuery({
    queryKey: [...keys.internals(sid, name), version],
    queryFn: () =>
      unwrap(api.GET("/api/sessions/{sid}/instruments/{name}/internals", { params: { path: { sid, name } } })),
    placeholderData: (prev) => prev,
    staleTime: Infinity,
  });
}

/**
 * The signal at an output port inside an instrument (signal-chain.md 6, #953): re-keyed on the event
 * version (D64), no polling. The previous answer stays on screen while a new version loads, but only
 * for the same port. A 403 (validation mode withholds it) is an answer, not a failure to retry.
 */
export function useInternalProbe(sid: string, name: string, port: string | null, version: number) {
  return useQuery({
    queryKey: [...keys.internals(sid, name), "probe", port ?? "", version],
    queryFn: () =>
      unwrap(
        api.GET("/api/sessions/{sid}/instruments/{name}/internals/probe", {
          params: { path: { sid, name }, query: { port: port! } },
        }),
      ),
    enabled: !!port,
    placeholderData: (prev, prevQuery) => (prevQuery?.queryKey[4] === port ? prev : undefined),
    retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
  });
}

/** A screen's readings wiring in one hook (issue 638): the event version subscription, the query,
 * the value lookups and the screen's state map — what eight screens each repeated. Freshness is
 * the event stream, decided in D64: the query re-keys on the version (a write, a state change, a
 * wall/scaled clock tick, a reconnect) and does not poll, so one command is one request; the
 * page-wide session and faults keys keep their 250 ms debounced invalidation instead. Scope (its
 * own query), the net probe, net levels and the internals view subscribe the same version for
 * queries that are not readings wiring — they keep their own (deferred) subscriptions. `defer`
 * follows the version one render behind. */
export function useInstrumentReadings(
  sid: string,
  inst: InstrumentInfo,
  { enabled = true, defer = false }: { enabled?: boolean; defer?: boolean } = {},
) {
  const version = useEvents((s) => s.version);
  const soft = useDeferredValue(version);
  const query = useReadings(sid, inst.name, defer ? soft : version, enabled);
  const values = useMemo(() => query.data?.values ?? {}, [query.data]);
  const vars = useMemo(() => stateMap(inst), [inst]);
  const num = useCallback((k: string) => Number(values[k] ?? 0), [values]);
  return { query, values, num, vars };
}

/** Front-panel readings; keyed by the event version, no polling (D64: the version moves on every
 * write, state change, clock tick and reconnect, so a re-key is exactly one request). */
export function useReadings(sid: string, name: string, version: number, enabled = true) {
  return useQuery({
    enabled,
    queryKey: [...keys.readings(sid, name), version],
    queryFn: () =>
      unwrap(api.GET("/api/sessions/{sid}/instruments/{name}/readings", { params: { path: { sid, name } } })),
    placeholderData: (prev) => prev,
  });
}

/** Run records in the record folder, newest first (D36). */
export function useRuns() {
  return useQuery({ queryKey: keys.runs, queryFn: () => unwrap(api.GET("/api/runs")), refetchInterval: 5000 });
}

export function useRun(id: string) {
  return useQuery({
    queryKey: keys.run(id),
    queryFn: () => unwrap(api.GET("/api/runs/{run_id}", { params: { path: { run_id: id } } })),
  });
}

/** Replay a run record and compare every reply. */
export function useReplayRun(id: string) {
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/runs/{run_id}/replay", { params: { path: { run_id: id } } })),
  });
}

/** A virtual probe on a net, refreshed whenever the session changes. */
export function useNetProbe(sid: string, net: string | null | undefined, version: number) {
  return useQuery({
    queryKey: ["probe", sid, net ?? "", version],
    queryFn: () => unwrap(api.GET("/api/sessions/{sid}/nets/{net}/probe", { params: { path: { sid, net: net! } } })),
    enabled: !!net,
    placeholderData: (prev) => prev,
  });
}

// ---- an instrument definition's acceptance page (T4, instrument-authoring.md 7.2) -----------------

/** The definition as stored (hash, diagnostics); polled so a change made elsewhere is noticed. */
export function useDefinitionDocument(id: string, pollMs: number | false = 2000) {
  return useQuery<DefinitionDocument>({
    queryKey: keys.definitionDoc(id),
    queryFn: () => unwrap(api.GET("/api/definitions/{def_id}/document", { params: { path: { def_id: id } } })),
    enabled: !!id,
    refetchInterval: pollMs,
  });
}

/** The definition as the engine loads it (defaults applied); fetched again when the file hash moves. */
async function fetchDefinition(id: string) {
  return unwrap(api.GET("/api/definitions/{def_id}", { params: { path: { def_id: id } } }));
}

/** A definition as the engine loads it, the shape every acceptance tab reads. */
export type DefinitionData = Awaited<ReturnType<typeof fetchDefinition>>;

export function useDefinition(id: string, sha = "") {
  return useQuery({
    queryKey: [...keys.definition(id), sha],
    queryFn: () => fetchDefinition(id),
    enabled: !!id,
    placeholderData: (prev) => prev,
    staleTime: Infinity,
  });
}

/** A real model's coverage table; null for definitions without one (D42). */
export function useDefinitionCoverage(id: string, sha = "") {
  return useQuery<DefinitionCoverage | null>({
    queryKey: [...keys.coverage(id), sha],
    queryFn: () => unwrap(api.GET("/api/definitions/{def_id}/coverage", { params: { path: { def_id: id } } })),
    enabled: !!id,
    placeholderData: (prev) => prev,
    staleTime: Infinity,
  });
}

/** Saved versions of a workspace definition, newest first, with what each one changed (T3). */
export function useDefinitionHistory(id: string, sha = "", enabled = true) {
  return useQuery<DefinitionVersion[]>({
    queryKey: [...keys.definitionHistory(id), sha],
    queryFn: () => unwrap(api.GET("/api/definitions/{def_id}/history", { params: { path: { def_id: id } } })),
    enabled: enabled && !!id,
    placeholderData: (prev) => prev,
    staleTime: Infinity,
  });
}

/** The changes between two saved versions (base 0: the creation); versions never change. */
export function useDefinitionChanges(id: string, base: number, target: number) {
  return useQuery<DefinitionChangeList>({
    queryKey: keys.definitionChanges(id, base, target),
    queryFn: () =>
      unwrap(
        api.GET("/api/definitions/{def_id}/history/changes", {
          params: { path: { def_id: id }, query: { base, target } },
        }),
      ),
    enabled: target > 0,
    staleTime: Infinity,
  });
}

/** The workspace's cases files whose model is this definition. */
export function useDefinitionCases(id: string) {
  return useQuery<CaseFile[]>({
    queryKey: keys.definitionCases(id),
    queryFn: () => unwrap(api.GET("/api/definitions/{def_id}/cases", { params: { path: { def_id: id } } })),
    enabled: !!id,
  });
}

/** Run a cases file: every case on a freshly powered bench, each step with expected and got. */
export function useRunDefinitionCases(id: string) {
  return useMutation<CaseRunResult, Error, string>({
    mutationFn: (path: string) =>
      unwrap(api.POST("/api/definitions/{def_id}/cases/run", { params: { path: { def_id: id } }, body: { path } })),
  });
}

/** Open (or reopen: it replaces the same definition's session) a try session. */
export function useOpenTry() {
  return useMutation<TryOpened, Error, string>({
    mutationFn: (defId: string) => unwrap(api.POST("/api/try/{def_id}", { params: { path: { def_id: defId } } })),
  });
}

export function useCloseTry() {
  return useMutation<void, Error, string>({
    mutationFn: (sid: string) => unwrap(api.DELETE("/api/try/{sid}", { params: { path: { sid } } })),
  });
}

/** The try session's instrument (identity, settings, panel, deviations); polled, which also keeps
 * the temporary session from being reaped while the page is open. */
export function useTryInstrument(sid: string | undefined, version = 0) {
  return useQuery<InstrumentInfo>({
    queryKey: [...keys.tryInstrument(sid ?? ""), version],
    queryFn: () => unwrap(api.GET("/api/try/{sid}/instrument", { params: { path: { sid: sid! } } })),
    enabled: !!sid,
    placeholderData: (prev) => prev,
    refetchInterval: 3000,
    retry: (count, err) => (err as { status?: number }).status !== 404 && count < 2,
  });
}

/** The try session's component graph (issue 20): the acceptance page's internals tab. */
export function useTryInternals(sid: string | undefined, version = 0) {
  return useQuery<InstrumentInternals>({
    queryKey: [...keys.tryInternals(sid ?? ""), version],
    queryFn: () => unwrap(api.GET("/api/try/{sid}/internals", { params: { path: { sid: sid! } } })),
    enabled: !!sid,
    placeholderData: (prev) => prev,
    staleTime: Infinity,
  });
}

/** One program message to the try session's instrument. */
export function useTryScpi(sid: string | undefined) {
  return useMutation<ScpiResponse, Error, string>({
    mutationFn: (command: string) =>
      unwrap(api.POST("/api/try/{sid}/scpi", { params: { path: { sid: sid! } }, body: { command } })),
  });
}

// ---- the AI assistant (#877, D66) -------------------------------------------------------------------

export const assistantKeys = {
  config: ["assistant-config"] as const,
  conversation: (bench: string) => ["assistant-conversation", bench] as const,
  rollback: (bench: string) => ["assistant-rollback", bench] as const,
};

export function useAssistantConfig() {
  return useQuery({ queryKey: assistantKeys.config, queryFn: () => unwrap(api.GET("/api/assistant/config")) });
}

export function useSaveAssistantConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["AssistantConfigUpdate"]) => unwrap(api.PUT("/api/assistant/config", { body })),
    onSuccess: (cfg) => {
      qc.setQueryData(assistantKeys.config, cfg);
      void qc.invalidateQueries({ queryKey: ["assistant-conversation"] });
    },
  });
}

export function useTestAssistant() {
  return useMutation({ mutationFn: () => unwrap(api.POST("/api/assistant/config/test")) });
}

export function useAssistantConversation(bench: string | null | undefined) {
  return useQuery({
    queryKey: assistantKeys.conversation(bench ?? ""),
    queryFn: () => unwrap(api.GET("/api/assistant/conversation", { params: { query: { bench: bench! } } })),
    enabled: !!bench,
  });
}

export function useSendAssistant(bench: string) {
  return useMutation({
    mutationFn: (text: string) => unwrap(api.POST("/api/assistant/messages", { body: { bench, text } })),
  });
}

export function useStopAssistant(bench: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/assistant/stop", { body: { bench } })),
    onSuccess: (conv) => qc.setQueryData(assistantKeys.conversation(bench), conv),
  });
}

export function useClearAssistant(bench: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.DELETE("/api/assistant/conversation", { params: { query: { bench } } })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: assistantKeys.conversation(bench) }),
  });
}

export function useRollbackPlan(bench: string, enabled: boolean) {
  return useQuery({
    queryKey: assistantKeys.rollback(bench),
    queryFn: () => unwrap(api.GET("/api/assistant/rollback", { params: { query: { bench } } })),
    enabled,
  });
}

export function useRollback(bench: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Omit<Schemas["RollbackRequest"], "bench">) =>
      unwrap(api.POST("/api/assistant/rollback", { body: { bench, ...body } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: assistantKeys.conversation(bench) });
      void qc.invalidateQueries({ queryKey: assistantKeys.rollback(bench) });
      void qc.invalidateQueries({ queryKey: keys.benchDoc(bench) });
      void qc.invalidateQueries({ queryKey: keys.sessions });
      void qc.invalidateQueries({ queryKey: keys.catalog });
    },
  });
}

/** Copy a whole bench into a new file (#874): instruments, wires, settings, layout. */
export function useCopyBench() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (path: string) => unwrap(api.POST("/api/benches/copy", { body: { path } })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.benches }),
  });
}

/** "Save as my template" (#875): the instrument becomes a definition of its own. */
export function useSaveTemplate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["SaveTemplateRequest"]) => unwrap(api.POST("/api/definitions/templates", { body })),
    onSuccess: (out) => {
      void qc.invalidateQueries({ queryKey: keys.catalog });
      qc.setQueryData(keys.benchDoc(out.bench.path), out.bench);
    },
  });
}
