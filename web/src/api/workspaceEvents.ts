// The workspace's event stream (/api/events, issue #1484): file versions and access-port
// clients, what no session owns. Its queries re-key on the revisions below — the readings
// mechanism of D64, pointed at the workspace — instead of polling.
import { type QueryClient, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { create } from "zustand";
import type { ConnectionState } from "./events";
import type { WorkspaceEvent } from "./generated/workspace-events";
import { getToken } from "./token";

interface WorkspaceStore {
  status: ConnectionState;
  /** Per workspace file: bumped on each file_changed naming its path. */
  fileRev: Record<string, number>;
  /** Bumped on each resync (events may have been missed): part of every file's revision, so
   * a file that never had an event re-reads too (#1526). */
  epoch: number;
  /** Bumped on any file_changed under instruments/: a definition's structure layers its
   * parents and the category default, so any definition file's edit can change it. */
  instrumentsRev: number;
  /** Bumped on clients_changed (a script connected to or left the ports) and sessions_changed
   * (a session started or stopped): the sessions domain moved. The sessions list re-keys on it,
   * the open session's detail invalidates on it. */
  sessionsRev: number;
}

export const useWorkspaceEvents = create<WorkspaceStore>(() => ({
  status: "connecting",
  fileRev: {},
  epoch: 0,
  instrumentsRev: 0,
  sessionsRev: 0,
}));

/** A definition's file in the workspace, as the server writes it (DefinitionFiles.rel_path). */
export const definitionPath = (id: string) => `instruments/${id}.yaml`;

/** A file's revision in a store state: its own events plus every resync. Both only grow, so a
 * revision never repeats and a re-key never lands on an older cached answer. */
export const revOf = (s: WorkspaceStore, path: string) => s.epoch + (s.fileRev[path] ?? 0);

/** The revision a path's queries key on right now — for cache writes (setQueryData), which must
 * hit the live entry, not the one the last revision left behind. */
export const fileRevOf = (path: string) => revOf(useWorkspaceEvents.getState(), path);

/** Events may have been missed: every workspace query re-keys and re-reads once. */
export const resync = () =>
  useWorkspaceEvents.setState((s) => ({
    epoch: s.epoch + 1,
    instrumentsRev: s.instrumentsRev + 1,
    sessionsRev: s.sessionsRev + 1,
  }));

/** On the socket's first open: did a query already read (or start reading) the server? Its
 * answer may predate a change announced before the socket listened, which is lost for good —
 * the watcher's scan stays silent about content a write point noted (#1526). */
export const readBeforeOpen = (qc: QueryClient) =>
  qc.getQueryCache().getAll().some((q) => q.state.dataUpdatedAt > 0 || q.state.fetchStatus === "fetching");

/** One event as the store's next state: the reducer the socket's messages go through. */
export function reduceWorkspaceEvent(s: WorkspaceStore, ev: WorkspaceEvent): Partial<WorkspaceStore> {
  if (ev.kind === "file_changed" && ev.path) {
    const path = ev.path;
    return {
      fileRev: { ...s.fileRev, [path]: (s.fileRev[path] ?? 0) + 1 },
      instrumentsRev: path.startsWith("instruments/") ? s.instrumentsRev + 1 : s.instrumentsRev,
    };
  }
  if (ev.kind === "clients_changed" || ev.kind === "sessions_changed") {
    return { sessionsRev: s.sessionsRev + 1 };
  }
  return {};
}

/**
 * Keep one WebSocket to the workspace's event stream; the app mounts it once (main.tsx).
 *
 * A reconnect's missed events are lost for good, so every revision advances once and the
 * re-keyed queries re-read — the same self-heal as the session stream's (D64). The first open
 * does so only when a query read the server before the socket listened (#1526): a change in
 * that gap was announced to nobody. Otherwise a bump would just double the first load.
 */
export function useWorkspaceStream() {
  const qc = useQueryClient();
  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;
    let attempt = 0;
    let timer: number | undefined;

    let opened = false;
    const connect = () => {
      const proto = window.location.protocol === "https:" ? "wss" : "ws";
      const url = `${proto}://${window.location.host}/api/events?token=${encodeURIComponent(getToken() ?? "")}`;
      ws = new WebSocket(url);
      ws.onopen = () => {
        useWorkspaceEvents.setState({ status: "connected" });
        if (opened || readBeforeOpen(qc)) resync();
        opened = true;
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
  }, [qc]);
}
