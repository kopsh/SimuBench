// The workspace's event stream (/api/events, issue #1484): file versions and access-port
// clients, what no session owns. Its queries re-key on the revisions below — the readings
// mechanism of D64, pointed at the workspace — instead of polling.
import { useEffect } from "react";
import { create } from "zustand";
import type { ConnectionState } from "./events";
import type { WorkspaceEvent } from "./generated/workspace-events";
import { getToken } from "./token";

interface WorkspaceStore {
  status: ConnectionState;
  /** Per workspace file: bumped on each file_changed naming its path. */
  fileRev: Record<string, number>;
  /** Bumped on any file_changed under instruments/: a definition's structure layers its
   * parents and the category default, so any definition file's edit can change it. */
  instrumentsRev: number;
  /** Bumped on any clients_changed: some session's connected scripts changed. */
  clientsEpoch: number;
}

export const useWorkspaceEvents = create<WorkspaceStore>(() => ({
  status: "connecting",
  fileRev: {},
  instrumentsRev: 0,
  clientsEpoch: 0,
}));

/** A definition's file in the workspace, as the server writes it (DefinitionFiles.rel_path). */
export const definitionPath = (id: string) => `instruments/${id}.yaml`;

/** The revision a path's queries key on right now — for cache writes (setQueryData), which must
 * hit the live entry, not the one the last revision left behind. */
export const fileRevOf = (path: string) => useWorkspaceEvents.getState().fileRev[path] ?? 0;

/** One event as the store's next state: the reducer the socket's messages go through. */
export function reduceWorkspaceEvent(s: WorkspaceStore, ev: WorkspaceEvent): Partial<WorkspaceStore> {
  if (ev.kind === "file_changed" && ev.path) {
    const path = ev.path;
    return {
      fileRev: { ...s.fileRev, [path]: (s.fileRev[path] ?? 0) + 1 },
      instrumentsRev: path.startsWith("instruments/") ? s.instrumentsRev + 1 : s.instrumentsRev,
    };
  }
  if (ev.kind === "clients_changed") return { clientsEpoch: s.clientsEpoch + 1 };
  return {};
}

/**
 * Keep one WebSocket to the workspace's event stream; the app mounts it once (main.tsx).
 *
 * A reconnect's missed events are lost for good, so every revision advances once and the
 * re-keyed queries re-read — the same self-heal as the session stream's (D64). The first open
 * must not: every query just mounted on revision 0, and a bump would double its first load.
 */
export function useWorkspaceStream() {
  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;
    let attempt = 0;
    let timer: number | undefined;

    const bumpAll = () =>
      useWorkspaceEvents.setState((s) => ({
        fileRev: Object.fromEntries(Object.keys(s.fileRev).map((path) => [path, s.fileRev[path] + 1])),
        instrumentsRev: s.instrumentsRev + 1,
        clientsEpoch: s.clientsEpoch + 1,
      }));

    const connect = () => {
      const proto = window.location.protocol === "https:" ? "wss" : "ws";
      const url = `${proto}://${window.location.host}/api/events?token=${encodeURIComponent(getToken() ?? "")}`;
      ws = new WebSocket(url);
      ws.onopen = () => {
        useWorkspaceEvents.setState({ status: "connected" });
        if (attempt > 0) bumpAll();
        attempt = 0;
      };
      ws.onmessage = (msg) => {
        useWorkspaceEvents.setState((s) => reduceWorkspaceEvent(s, JSON.parse(msg.data as string) as WorkspaceEvent));
      };
      ws.onclose = (e) => {
        if (closed) return;
        if (e.code === 4401) return useWorkspaceEvents.setState({ status: "unauthorized" });
        useWorkspaceEvents.setState({ status: "reconnecting" });
        attempt += 1;
        timer = window.setTimeout(connect, Math.min(10_000, 500 * 2 ** Math.min(attempt, 5)));
      };
    };
    connect();
    return () => {
      closed = true;
      if (timer) window.clearTimeout(timer);
      ws?.close();
    };
  }, []);
}
