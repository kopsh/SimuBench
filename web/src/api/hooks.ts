import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FaultSpec, api, unwrap } from "./client";

export const keys = {
  benches: ["benches"] as const,
  sessions: ["sessions"] as const,
  session: (sid: string) => ["session", sid] as const,
  screen: (sid: string, name: string, source: string) => ["screen", sid, name, source] as const,
  catalog: ["catalog"] as const,
  faults: (sid: string) => ["faults", sid] as const,
  benchDoc: (path: string) => ["bench-doc", path] as const,
  preview: (path: string, sha: string) => ["bench-preview", path, sha] as const,
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
