import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FaultSpec, api, unwrap } from "./client";

export const keys = {
  benches: ["benches"] as const,
  sessions: ["sessions"] as const,
  session: (sid: string) => ["session", sid] as const,
  screen: (sid: string, name: string, source: string) => ["screen", sid, name, source] as const,
  catalog: ["catalog"] as const,
  faults: (sid: string) => ["faults", sid] as const,
  readings: (sid: string, name: string) => ["readings", sid, name] as const,
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
  return useQuery({ queryKey: keys.sessions, queryFn: () => unwrap(api.GET("/api/sessions")) });
}

export function useSession(sid: string) {
  return useQuery({
    queryKey: keys.session(sid),
    queryFn: () => unwrap(api.GET("/api/sessions/{sid}", { params: { path: { sid } } })),
    retry: (count, err) => (err as { status?: number }).status !== 404 && count < 3,
  });
}

export function useStartSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (bench_path: string) => unwrap(api.POST("/api/sessions", { body: { bench_path } })),
    onSuccess: (s) => {
      qc.setQueryData(keys.session(s.id), s);
      void qc.invalidateQueries({ queryKey: keys.sessions });
    },
  });
}

export function useStopSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (sid: string) => unwrap(api.DELETE("/api/sessions/{sid}", { params: { path: { sid } } })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.sessions }),
  });
}

export function useScpi(sid: string, name: string) {
  return useMutation({
    mutationFn: (command: string) =>
      unwrap(api.POST("/api/sessions/{sid}/instruments/{name}/scpi", { params: { path: { sid, name } }, body: { command } })),
  });
}

export function useConfigure(sid: string) {
  return useMutation({
    mutationFn: ({ instrument, path, value }: { instrument: string; path: string; value: unknown }) =>
      unwrap(
        api.POST("/api/sessions/{sid}/instruments/{name}/configure", {
          params: { path: { sid, name: instrument } },
          body: { path, value },
        }),
      ),
  });
}

export function useAdvance(sid: string) {
  return useMutation({
    mutationFn: (seconds: number) =>
      unwrap(api.POST("/api/sessions/{sid}/clock/advance", { params: { path: { sid } }, body: { seconds } })),
  });
}

export function useScopeScreen(sid: string, name: string, source: "preview" | "record", version: number) {
  return useQuery({
    queryKey: [...keys.screen(sid, name, source), version],
    queryFn: () =>
      unwrap(
        api.GET("/api/sessions/{sid}/instruments/{name}/screen", {
          params: { path: { sid, name }, query: { source } },
        }),
      ),
    placeholderData: (prev) => prev,
    staleTime: Infinity,
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
export function useReadings(sid: string, name: string, version: number) {
  return useQuery({
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
