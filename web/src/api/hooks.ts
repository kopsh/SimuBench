import { type QueryClient, useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useDeferredValue, useEffect, useMemo, useRef } from "react";
import { useEvents } from "./events";
import i18n from "@/i18n";
import { toast } from "sonner";
import { confirmClients, remoteInstrument } from "@/components/app/ClientsConfirm";
import { stateMap } from "@/features/workbench/model";
import type {
  CaseFile,
  IdentityUpdate,
  NewDefinitionRequest,
  DefinitionDocument,
  CaseRunResult,
  DefinitionChangeList,
  DefinitionCoverage,
  DefinitionVersion,
  InstrumentInfo,
  InstrumentInternals,
  ScpiResponse,
  SessionInfo,
  TryOpened,
} from "./client";
import { ApiError, type FaultSpec, type Schemas, api, unwrap } from "./client";
import { withValues } from "./sessionValues";
import { definitionPath, fileRevOf, revOf, useWorkspaceEvents } from "./workspaceEvents";

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
  storage: ["storage"] as const,
  run: (id: string) => ["run", id] as const,
  definition: (id: string) => ["definition", id] as const,
  definitionDoc: (id: string) => ["definition-document", id] as const,
  coverage: (id: string) => ["definition-coverage", id] as const,
  definitionHistory: (id: string) => ["definition-history", id] as const,
  definitionChanges: (id: string, base: number, target: number) => ["definition-changes", id, base, target] as const,
  definitionCases: (id: string) => ["definition-cases", id] as const,
  tryInstrument: (sid: string) => ["try-instrument", sid] as const,
  tryInternals: (sid: string) => ["try-internals", sid] as const,
  components: ["components"] as const,
  definitionStructure: (id: string) => ["definition-structure", id] as const,
  definitionLayout: (id: string) => ["definition-layout", id] as const,
};

export function useCatalog() {
  return useQuery({ queryKey: keys.catalog, queryFn: () => unwrap(api.GET("/api/catalog")), staleTime: 60_000 });
}

/**
 * The bench file as stored. Freshness is the workspace stream (issue #1484, the readings
 * mechanism of D64 pointed at files): the query re-keys on the file's revision, so one
 * file_changed — a write through the server's write points or a change the watcher noticed on
 * disk (an editor, a second service) — is exactly one request, and nothing polls. The previous
 * answer stays on screen while the next loads.
 */
export function useBenchDoc(path: string | null | undefined) {
  const rev = useWorkspaceEvents((s) => (path ? revOf(s, path) : 0));
  return useQuery({
    queryKey: [...keys.benchDoc(path ?? ""), rev] as const,
    queryFn: () => unwrap(api.GET("/api/benches/file", { params: { query: { path: path! } } })),
    enabled: !!path,
    placeholderData: (prev) => prev,
  });
}

/** The bench doc's cache key at the revision now known: a cache write must hit the live entry. */
export const benchDocKey = (path: string) => [...keys.benchDoc(path), fileRevOf(path)] as const;

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
  // The workspace stream announces what the list shows and no session owns: a script's connect
  // and disconnect (clients_changed) and a session's start and stop, whoever caused them
  // (#1484). The list re-keys on it instead of polling.
  const sessionsRev = useWorkspaceEvents((s) => s.sessionsRev);
  return useQuery({
    queryKey: [...keys.sessions, sessionsRev] as const,
    queryFn: () => unwrap(api.GET("/api/sessions")),
    placeholderData: (prev) => prev,
  });
}

export function useSession(sid: string) {
  const qc = useQueryClient();
  // The stream's clients_changed and sessions_changed retires the 2–5 s compensation poll
  // (#121, #1484): remote marks stay fresh, and a session stopped under this page (the
  // assistant's restart, another client) refetches into its 404. This key keeps its bare form —
  // the state patch (WorkbenchPage) writes it directly — so freshness arrives as an
  // invalidation, not a re-key.
  const sessionsRev = useWorkspaceEvents((s) => s.sessionsRev);
  const seenEpoch = useRef(sessionsRev);
  useEffect(() => {
    if (sessionsRev === seenEpoch.current) return;
    seenEpoch.current = sessionsRev;
    void qc.invalidateQueries({ queryKey: keys.session(sid) });
  }, [sessionsRev, qc, sid]);
  return useQuery({
    queryKey: keys.session(sid),
    queryFn: () => fetchSession(qc, sid),
    retry: (count, err) => (err as { status?: number }).status !== 404 && count < 3,
  });
}

/**
 * The session: in full the first time and after the bench is built again, else only its values laid
 * onto the one held (#1299: a scope's full session is about 1 MB, polled every few seconds).
 */
async function fetchSession(qc: QueryClient, sid: string): Promise<SessionInfo> {
  const held = qc.getQueryData<SessionInfo>(keys.session(sid));
  if (held?.build) {
    const light = await unwrap(api.GET("/api/sessions/{sid}", { params: { path: { sid }, query: { state: "values" } } }));
    const merged = withValues(held, light);
    if (merged) return merged;
  }
  return unwrap(api.GET("/api/sessions/{sid}", { params: { path: { sid } } }));
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

/** The templates a new instrument can start from (D3 layer 2, P2-5). */
export function useInstrumentTemplates(enabled = true) {
  return useQuery({
    queryKey: ["instrument-templates"],
    queryFn: () => unwrap(api.GET("/api/instrument-templates")),
    staleTime: Infinity,
    enabled,
  });
}

/** A new workspace definition from a template's inputs or blank (P2-5). Unsaved inputs come back
 * with their diagnostics, not as an error. */
export function useNewDefinition() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: NewDefinitionRequest) => unwrap(api.POST("/api/definitions/new", { body })),
    onSuccess: (r) => {
      if (r.saved) void qc.invalidateQueries({ queryKey: keys.catalog });
    },
  });
}

/** A workspace definition's maker, model, serial, firmware and *IDN? format (P2-5). */
export function useSaveIdentity(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: IdentityUpdate) =>
      unwrap(api.PUT("/api/definitions/{def_id}/identity", { params: { path: { def_id: id } }, body })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.definitionDoc(id) });
      void qc.invalidateQueries({ queryKey: keys.definition(id) });
      void qc.invalidateQueries({ queryKey: keys.catalog });
    },
  });
}

/** Every component type: the build area's library (#1201). */
export function useComponents() {
  return useQuery({ queryKey: keys.components, queryFn: () => unwrap(api.GET("/api/components")), staleTime: Infinity });
}

/**
 * A definition's effective structure for the build area (#1201), keyed on the workspace
 * stream's instruments revision (issue #1484): the structure layers the definition's parents
 * and the category default in, so any definition file's edit can change it — one file_changed
 * under instruments/ is one request, no polling. The previous answer stays while the next loads.
 */
export function useDefinitionStructure(id: string) {
  const rev = useWorkspaceEvents((s) => s.instrumentsRev);
  return useQuery({
    queryKey: [...keys.definitionStructure(id), rev] as const,
    queryFn: () =>
      unwrap(api.GET("/api/definitions/{def_id}/structure", { params: { path: { def_id: id } } })),
    enabled: !!id,
    placeholderData: (prev) => prev,
  });
}

/** The structure's cache key at the revision now known: a cache write must hit the live entry. */
export const definitionStructureKey = (id: string) => [...keys.definitionStructure(id), useWorkspaceEvents.getState().instrumentsRev] as const;

/** Where the build area draws the components of a definition (#1207). */
export function useDefinitionLayout(id: string) {
  return useQuery({
    queryKey: keys.definitionLayout(id),
    queryFn: () => unwrap(api.GET("/api/definitions/{def_id}/layout", { params: { path: { def_id: id } } })),
    enabled: !!id,
    staleTime: Infinity,
  });
}

/** Keep the positions beside a workspace definition: no version, no session restart. */
export function useSaveDefinitionLayout(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["DefinitionLayout-Input"]) =>
      unwrap(api.PUT("/api/definitions/{def_id}/layout", { params: { path: { def_id: id } }, body })),
    onSuccess: (layout) => qc.setQueryData(keys.definitionLayout(id), layout),
  });
}

/** Save the structure the build area drew: one version of the definition (D35). */
export function useSaveDefinitionStructure(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["StructureUpdate"]) =>
      unwrap(api.PUT("/api/definitions/{def_id}/structure", { params: { path: { def_id: id } }, body })),
    onSuccess: (structure) => {
      qc.setQueryData(definitionStructureKey(id), structure);
      void qc.invalidateQueries({ queryKey: keys.definitionDoc(id) });
      void qc.invalidateQueries({ queryKey: keys.definitionHistory(id) });
    },
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
      // A taken default port was replaced by a free one (#1197): scripts must use the shown address.
      for (const notice of s.port_notices ?? []) toast.warning(notice, { duration: 15000 });
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

/**
 * Switch the running clock (#1660): Live (wall, or scaled at a rate) and Pause (back to a clock
 * that moves only on commands). The cached session takes the new mode at once, so the top bar and
 * the screens' hints follow without waiting for the event's refetch.
 */
export function useClockMode(sid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ mode, scale }: { mode: Schemas["ClockModeRequest"]["mode"]; scale?: number }) =>
      confirmClients("clock", (force) =>
        unwrap(api.POST("/api/sessions/{sid}/clock/mode", { params: { path: { sid } }, body: { mode, scale, force } })),
      ),
    onSuccess: (clock) => {
      qc.setQueryData<SessionInfo>(keys.session(sid), (s) =>
        s ? { ...s, clock_mode: clock.mode, time_scale: clock.scale, now_ns: Math.max(s.now_ns, clock.now_ns) } : s,
      );
      void qc.invalidateQueries({ queryKey: keys.session(sid) });
    },
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
 * `tuneHz`: where a receiver is tuned for this read (#959); left out, its center frequency. The
 * previous tuning's answer stays on screen while the next one loads, so retuning does not flicker.
 */
export function useInternalProbe(sid: string, name: string, port: string | null, version: number, tuneHz?: number) {
  return useQuery({ ...internalProbeQuery(sid, name, port ?? "", version, tuneHz), enabled: !!port });
}

/** One port's probe as a query: the panel and the diagram's thumbnails share it, so opening a port already on screen is instant. */
function internalProbeQuery(sid: string, name: string, port: string, version: number, tuneHz?: number) {
  return {
    // The center frequency's read has no tuning in its key: it is the thumbnails' read too.
    queryKey: [...keys.internals(sid, name), "probe", port, version, ...(tuneHz == null ? [] : [tuneHz])] as const,
    queryFn: () =>
      unwrap(
        api.GET("/api/sessions/{sid}/instruments/{name}/internals/probe", {
          params: { path: { sid, name }, query: tuneHz == null ? { port } : { port, tune_hz: tuneHz } },
        }),
      ),
    placeholderData: (prev: Schemas["InternalProbe"] | undefined, prevQuery: { queryKey: readonly unknown[] } | undefined) =>
      prevQuery?.queryKey[4] === port ? prev : undefined,
    retry: (count: number, error: unknown) => !(error instanceof ApiError && error.status < 500) && count < 2,
  };
}

/**
 * The signal at every listed output port (the thumbnails on the internals diagram, #953): one probe
 * per port, re-keyed on the event version like the panel's. `withheld` when the service refuses
 * (validation mode, 403) — the caller then stops asking.
 */
export function useInternalProbes(sid: string, name: string, ports: readonly string[], version: number, enabled = true) {
  const results = useQueries({ queries: ports.map((port) => ({ ...internalProbeQuery(sid, name, port, version), enabled })) });
  // A new map only when an answer changed: every block on the diagram renders from it.
  const stamp = results.map((r, i) => `${ports[i]}:${r.dataUpdatedAt}:${r.errorUpdatedAt}`).join("|");
  return useMemo(() => {
    const probes = new Map<string, Schemas["InternalProbe"]>();
    results.forEach((r, i) => {
      if (r.data) probes.set(ports[i], r.data);
    });
    return { probes, withheld: results.some((r) => r.error instanceof ApiError && r.error.status === 403) };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the answers, by when they arrived
  }, [stamp]);
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

/** What the workspace's `.simubench/` holds and which of it lost its file (#1238). */
export function useStorage(enabled: boolean) {
  return useQuery({ queryKey: keys.storage, queryFn: () => unwrap(api.GET("/api/storage")), enabled });
}

/** Delete what lost its file: conversations and instrument files, saved versions only with `history`. */
export function useCleanStorage() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (history: boolean) => unwrap(api.POST("/api/storage/clean", { body: { history } })),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: keys.storage });
      void qc.invalidateQueries({ queryKey: ["doctor"] });
    },
  });
}

/**
 * How often a progress domain is re-read while it works (#1484): the runs list gaining a record,
 * a try session's instrument (whose poll also keeps the session from being reaped), the
 * assistant's conversation advancing. These watch the server's asynchronous work, which the
 * workspace stream does not carry — the file it will write does, the progress does not — so one
 * named interval decides for all three instead of three private numbers. The freshness of
 * everything a file or a port answers is the stream's (D64, #1484).
 */
export const PROGRESS_POLL_MS = 3000;

/** Run records in the record folder, newest first (D36). */
export function useRuns() {
  return useQuery({ queryKey: keys.runs, queryFn: () => unwrap(api.GET("/api/runs")), refetchInterval: PROGRESS_POLL_MS });
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

/**
 * The definition as stored (hash, diagnostics), keyed on the file's revision from the workspace
 * stream (issue #1484): one file_changed for its file is one request, no polling. Built-in
 * definitions have no workspace file, so only a resync (a reconnect) moves their revision.
 */
export function useDefinitionDocument(id: string) {
  const rev = useWorkspaceEvents((s) => revOf(s, definitionPath(id)));
  return useQuery<DefinitionDocument>({
    queryKey: [...keys.definitionDoc(id), rev] as const,
    queryFn: () => unwrap(api.GET("/api/definitions/{def_id}/document", { params: { path: { def_id: id } } })),
    enabled: !!id,
    placeholderData: (prev) => prev,
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

/** The try session's instrument (identity, settings, panel, deviations); polled (PROGRESS_POLL_MS),
 * which also keeps the temporary session from being reaped while the page is open. */
export function useTryInstrument(sid: string | undefined, version = 0) {
  return useQuery<InstrumentInfo>({
    queryKey: [...keys.tryInstrument(sid ?? ""), version],
    queryFn: () => unwrap(api.GET("/api/try/{sid}/instrument", { params: { path: { sid: sid! } } })),
    enabled: !!sid,
    placeholderData: (prev) => prev,
    refetchInterval: PROGRESS_POLL_MS,
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

/** Send a message; `stopConsent` is the answer to the assistant's request to stop the running
 * bench, given with the panel's buttons (#902) — a typed message carries none. */
export function useSendAssistant(bench: string) {
  return useMutation({
    mutationFn: ({ text, stopConsent }: { text: string; stopConsent?: boolean }) =>
      unwrap(api.POST("/api/assistant/messages", { body: { bench, text, stop_consent: stopConsent ?? null } })),
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
      qc.setQueryData(benchDocKey(out.bench.path), out.bench);
    },
  });
}
