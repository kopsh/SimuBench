// The workspace's event stream (/api/events, issue #1484): file versions and access-port
// clients, what no session owns. Its queries re-key on the revisions below — the readings
// mechanism of D64, pointed at the workspace — instead of polling.
import { type QueryClient, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { create } from "zustand";
import type { WorkspaceEvent } from "./generated/workspace-events";
import { openWsStream, wsUrl, type ConnectionState } from "./wsTransport";

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

/** A folder's revision in a store state: every event under the prefix plus every resync — what
 * a query listing the folder's files keys on, since any one of them can change the list (#1735). */
export const revUnder = (s: WorkspaceStore, prefix: string) =>
  s.epoch + Object.entries(s.fileRev).reduce((n, [p, v]) => (p.startsWith(prefix) ? n + v : n), 0);

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
    let opened = false;
    return openWsStream(() => wsUrl("/api/events"), {
      onOpen: () => {
        useWorkspaceEvents.setState({ status: "connected" });
        if (opened || readBeforeOpen(qc)) resync();
        opened = true;
      },
      onMessage: (data) => {
        useWorkspaceEvents.setState((s) => reduceWorkspaceEvent(s, JSON.parse(data) as WorkspaceEvent));
      },
      onStatus: (status) => useWorkspaceEvents.setState({ status }),
    });
  }, [qc]);
}
