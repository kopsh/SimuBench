import { type QueryClient, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import i18n from "@/i18n";
import { toast } from "sonner";
import { confirmClients, remoteInstrument } from "@/components/app/ClientsConfirm";
import { type FaultSpec, api, unwrap } from "./client";

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

/** Front-panel readings; refreshed whenever the session's events move (and once a second). */
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

export function useReadings(sid: string, name: string, version: number, enabled = true) {
  return useQuery({
    enabled,
    queryKey: [...keys.readings(sid, name), version],
    queryFn: () =>
      unwrap(api.GET("/api/sessions/{sid}/instruments/{name}/readings", { params: { path: { sid, name } } })),
    placeholderData: (prev) => prev,
    refetchInterval: 1000,
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
