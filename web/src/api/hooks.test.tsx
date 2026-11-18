import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { InstrumentInfo } from "./client";
import { api } from "./client";
import { useEvents } from "./events";
import { useBenchDoc, useDefinitionDocument, useDefinitionStructure, useInstrumentReadings, useSessions } from "./hooks";
import { reduceWorkspaceEvent, useWorkspaceEvents } from "./workspaceEvents";

afterEach(() => {
  vi.restoreAllMocks();
  useEvents.setState({ version: 0 });
});

const INST = {
  name: "smu",
  label: null,
  model: "B2901A",
  state: [{ path: "source.function", value: "VOLT" }],
} as unknown as InstrumentInfo;

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

// D64: the readings query rides the event version — one bump is one request, and nothing polls
// between bumps. Before #694 a 1 s refetchInterval doubled every command's request and kept the
// endpoint busy while idle.
it("one version bump is one readings request; the idle gap does not poll", async () => {
  const GET = vi.spyOn(api, "GET").mockResolvedValue({
    data: { instrument: "smu", now_ns: 0, values: {}, traces: [] },
    error: undefined,
    response: new Response(),
  });
  const { result } = renderHook(() => useInstrumentReadings("s1", INST), { wrapper });
  await waitFor(() => expect(GET).toHaveBeenCalledTimes(1)); // the mount fetch
  expect(result.current.vars.get("source.function")?.value).toBe("VOLT"); // the state map rides along

  act(() => useEvents.setState({ version: 5 })); // the one command's event batch
  await waitFor(() => expect(GET).toHaveBeenCalledTimes(2));

  await new Promise((r) => setTimeout(r, 1100)); // longer than the old 1 s poll
  expect(GET).toHaveBeenCalledTimes(2); // the idle gap adds nothing

  act(() => useEvents.setState({ version: 6 })); // the next command
  await waitFor(() => expect(GET).toHaveBeenCalledTimes(3));
});

// The workspace stream's freshness (#1484): one file_changed is one request for the file it
// names, and nothing polls between events — the D64 mechanism pointed at the workspace. Before
// it, the file and session queries polled every 2–3 s each.
describe("workspace event re-key", () => {
  afterEach(() => {
    useWorkspaceEvents.setState({ fileRev: {}, instrumentsRev: 0, clientsEpoch: 0 });
  });

  function mockApi(paths: Record<string, unknown>) {
    return vi.spyOn(api, "GET").mockImplementation(async (url: string) => {
      if (!(url in paths)) throw new Error(`unexpected GET ${url}`);
      return { data: paths[url], error: undefined, response: new Response() } as never;
    });
  }

  it("a bench file's file_changed re-keys useBenchDoc; another file's does not", async () => {
    const GET = mockApi({ "/api/benches/file": { path: "a.bench.yaml", data: {}, ok: true, diagnostics: [] } });
    const { result } = renderHook(() => useBenchDoc("a.bench.yaml"), { wrapper });
    await waitFor(() => expect(GET).toHaveBeenCalledTimes(1)); // the mount fetch

    act(() => useWorkspaceEvents.setState({ fileRev: { "a.bench.yaml": 1 } })); // its file_changed
    await waitFor(() => expect(GET).toHaveBeenCalledTimes(2));

    act(() => useWorkspaceEvents.setState((s) => ({ fileRev: { ...s.fileRev, "other.bench.yaml": 1 } })));
    expect(GET).toHaveBeenCalledTimes(2); // another file's change costs this query nothing

    await new Promise((r) => setTimeout(r, 2600)); // longer than the old 2.5 s poll
    expect(GET).toHaveBeenCalledTimes(2); // the idle gap adds nothing
    expect(result.current.data).toBeDefined(); // the previous answer stayed while re-keying
  });

  it("the sessions list re-keys on clients_changed", async () => {
    const GET = mockApi({ "/api/sessions": [] });
    renderHook(() => useSessions(), { wrapper });
    await waitFor(() => expect(GET).toHaveBeenCalledTimes(1));

    act(() => useWorkspaceEvents.setState({ clientsEpoch: 1 })); // a script connected or left
    await waitFor(() => expect(GET).toHaveBeenCalledTimes(2));

    await new Promise((r) => setTimeout(r, 2100)); // longer than the old 2 s poll
    expect(GET).toHaveBeenCalledTimes(2);
  });

  it("definition queries re-key: the structure on any instruments/ write, the document on its own file", async () => {
    const GET = mockApi({
      "/api/definitions/{def_id}/structure": { id: "sb-dso-4", components: [], ports: [], wires: [] },
      "/api/definitions/{def_id}/document": { id: "sb-dso-4", editable: false, data: {}, ok: true, diagnostics: [], changes: [] },
    });
    renderHook(() => useDefinitionStructure("sb-dso-4"), { wrapper });
    renderHook(() => useDefinitionDocument("sb-dso-4"), { wrapper });
    await waitFor(() => expect(GET).toHaveBeenCalledTimes(2)); // one mount fetch each

    act(() => useWorkspaceEvents.setState((s) => ({ fileRev: { ...s.fileRev, "benches/x.bench.yaml": 3 } })));
    expect(GET).toHaveBeenCalledTimes(2); // a bench file's change touches neither

    act(() => useWorkspaceEvents.setState((s) => ({
      fileRev: { ...s.fileRev, "instruments/sb-dso-4.yaml": 1 },
      instrumentsRev: s.instrumentsRev + 1,
    })));
    await waitFor(() => expect(GET).toHaveBeenCalledTimes(4)); // both re-read: the document's own
    // file moved, and the structure layers its parents, so any instruments/ write counts.

    act(() => useWorkspaceEvents.setState((s) => ({
      fileRev: { ...s.fileRev, "instruments/other.yaml": 1 },
      instrumentsRev: s.instrumentsRev + 1,
    })));
    await waitFor(() => expect(GET).toHaveBeenCalledTimes(5)); // the structure again, alone
    expect(GET).toHaveBeenCalledTimes(5);
  });

  it("the reducer: a definition file's edit moves both revisions, a bench file's only its own", () => {
    const s = { status: "connected", fileRev: { "a.bench.yaml": 2 }, instrumentsRev: 5, clientsEpoch: 1 } as const;
    expect(reduceWorkspaceEvent(s, { kind: "file_changed", seq: 1, path: "a.bench.yaml" })).toEqual({
      fileRev: { "a.bench.yaml": 3 },
      instrumentsRev: 5,
    });
    expect(
      reduceWorkspaceEvent(s, { kind: "file_changed", seq: 2, path: "instruments/my-dso.yaml" }),
    ).toEqual({ fileRev: { ...s.fileRev, "instruments/my-dso.yaml": 1 }, instrumentsRev: 6 });
    expect(reduceWorkspaceEvent(s, { kind: "clients_changed", seq: 3, sid: "s1" })).toEqual({ clientsEpoch: 2 });
    expect(reduceWorkspaceEvent(s, { kind: "file_changed", seq: 4 })).toEqual({}); // no path, no move
  });
});
