import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, expect, it, vi } from "vitest";
import type { InstrumentInfo } from "./client";
import { api } from "./client";
import { useEvents } from "./events";
import { useInstrumentReadings } from "./hooks";

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
