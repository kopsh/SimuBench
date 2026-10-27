import type { QueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { create } from "zustand";
import { ApiError, type BenchData, type BenchDocument, type EditOp, api, unwrap } from "@/api/client";
import { keys } from "@/api/hooks";
import i18n from "@/i18n";
import { useWorkbench } from "../store";
import { describeChange } from "./diff";

const MAX_UNDO = 100;

interface EditorState {
  path: string | null;
  doc: BenchDocument | null;
  undo: BenchData[];
  redo: BenchData[];
  /** Operations sent and not yet answered. */
  pending: number;
  /** A save failed for a reason other than a conflict; the canvas shows the file as it is. */
  lastError: string | null;
}

export const useEditor = create<EditorState>(() => ({
  path: null,
  doc: null,
  undo: [],
  redo: [],
  pending: 0,
  lastError: null,
}));

let queryClient: QueryClient | null = null;
// Hashes this window has already moved past. A poll answered after one of our own saves can
// still carry the old hash; that is not a change made elsewhere.
const superseded = new Set<string>();
// Edits are applied one after another, each against the hash the previous one produced.
let chain: Promise<unknown> = Promise.resolve();

export function bindEditor(qc: QueryClient) {
  queryClient = qc;
}

function publish(doc: BenchDocument) {
  const old = useEditor.getState().doc;
  if (old && old.sha256 !== doc.sha256) superseded.add(old.sha256);
  useEditor.setState({ doc });
  queryClient?.setQueryData(keys.benchDoc(doc.path), doc);
}

/** Take the document the server has; called on load and whenever polling sees another hash. */
export function syncDocument(doc: BenchDocument) {
  const s = useEditor.getState();
  if (s.path !== doc.path) {
    superseded.clear();
    useEditor.setState({ path: doc.path, doc, undo: [], redo: [], lastError: null });
    return;
  }
  if (s.pending > 0 || s.doc?.sha256 === doc.sha256 || superseded.has(doc.sha256)) return;
  if (s.doc) superseded.add(s.doc.sha256);
  // Changed outside this window (an agent, a text editor, another tab). Our history would
  // overwrite that change, so it starts afresh.
  const summary = s.doc ? describeChange(s.doc.data, doc.data) : "";
  useEditor.setState({ doc, undo: [], redo: [] });
  if (s.doc) {
    toast.info(i18n.t("edit.externalChange"), {
      description: summary || undefined,
      action: { label: i18n.t("history.review"), onClick: () => useWorkbench.getState().setHistory(true) },
    });
  }
}

async function reload(path: string) {
  const doc = await unwrap(api.GET("/api/benches/file", { params: { query: { path } } }));
  useEditor.setState({ undo: [], redo: [] });
  publish(doc);
}

function enqueue<T>(task: () => Promise<T>): Promise<T | undefined> {
  useEditor.setState((s) => ({ pending: s.pending + 1 }));
  const run = chain.then(task).catch(async (e: unknown) => {
    const path = useEditor.getState().path;
    if (e instanceof ApiError && e.status === 409 && path) {
      toast.warning(i18n.t("edit.conflict"));
      await reload(path).catch(() => undefined);
    } else {
      const message = e instanceof Error ? e.message : String(e);
      useEditor.setState({ lastError: message });
      toast.error(i18n.t("edit.failed"), { description: message });
    }
    return undefined;
  });
  chain = run.finally(() => useEditor.setState((s) => ({ pending: s.pending - 1 })));
  return run;
}

/** Apply edit operations to the bench file; resolves to the new document, or undefined on failure. */
export function applyOps(ops: EditOp[]): Promise<BenchDocument | undefined> {
  return enqueue(async () => {
    const { doc, path } = useEditor.getState();
    if (!doc || !path) throw new Error("no bench file");
    const next = await unwrap(api.POST("/api/benches/edit", { body: { path, base_sha256: doc.sha256, ops, source: "ui" } }));
    useEditor.setState((s) => ({ undo: [...s.undo, doc.data].slice(-MAX_UNDO), redo: [], lastError: null }));
    publish(next);
    return next;
  });
}

async function put(data: BenchData): Promise<BenchDocument> {
  const { doc, path } = useEditor.getState();
  if (!doc || !path) throw new Error("no bench file");
  return unwrap(api.PUT("/api/benches/file", { body: { path, base_sha256: doc.sha256, data, source: "ui" } }));
}

export function undo() {
  return enqueue(async () => {
    const { undo: stack, doc } = useEditor.getState();
    const prev = stack.at(-1);
    if (!prev || !doc) return;
    const next = await put(prev);
    useEditor.setState((s) => ({ undo: s.undo.slice(0, -1), redo: [...s.redo, doc.data] }));
    publish(next);
  });
}

export function redo() {
  return enqueue(async () => {
    const { redo: stack, doc } = useEditor.getState();
    const nextData = stack.at(-1);
    if (!nextData || !doc) return;
    const next = await put(nextData);
    useEditor.setState((s) => ({ redo: s.redo.slice(0, -1), undo: [...s.undo, doc.data] }));
    publish(next);
  });
}

/** Write saved version ``seq`` back as the newest version (D35); undo returns to the file before it. */
export function restoreVersion(seq: number): Promise<BenchDocument | undefined> {
  return enqueue(async () => {
    const { doc, path } = useEditor.getState();
    if (!doc || !path) throw new Error("no bench file");
    const next = await unwrap(api.POST("/api/benches/restore", { body: { path, seq, base_sha256: doc.sha256 } }));
    useEditor.setState((s) => ({ undo: [...s.undo, doc.data].slice(-MAX_UNDO), redo: [], lastError: null }));
    publish(next);
    return next;
  });
}

/** Wait until every queued edit has been answered (before restarting a session). */
export function settled(): Promise<unknown> {
  return chain;
}
