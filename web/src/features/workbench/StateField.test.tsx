import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { StateVarInfo } from "@/api/client";
import { api } from "@/api/client";
import { StateField } from "./StateField";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const RANGE = {
  path: "sense.range",
  label: "量程",
  type: "float",
  unit: "V",
  value: 0,
} as unknown as StateVarInfo;

function setup() {
  return vi.spyOn(api, "POST").mockResolvedValue({
    data: { ok: true, error: null, value: null },
    error: undefined,
    response: new Response(),
  });
}

const body = (call: number) =>
  (vi.mocked(api.POST).mock.calls[call]?.[1] as { body: { path: string; value: number } } | undefined)?.body;

// The stepper right after a typed commit (#835): the state event lags the POST, so the field's live
// value is still the old one. The step must build on the value just committed, not on the stale one
// — on CI it stepped 0 → 1 (unchanged on the instrument) and the field never reached 1.1 V.
it("a step right after a typed commit builds on the committed value while the live value lags", async () => {
  const POST = setup();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <StateField sid="s1" instrument="dmm" v={RANGE} />
    </QueryClientProvider>,
  );
  const input = screen.getByLabelText("量程") as HTMLInputElement;
  expect(input.value).toBe("0 V");

  input.focus();
  fireEvent.change(input, { target: { value: "1" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(POST).toHaveBeenCalledTimes(1));
  expect(body(0)).toEqual({ path: "sense.range", value: 1 });

  // The live value has not been re-read yet (v.value is still 0) — step anyway.
  fireEvent.keyDown(input, { key: "ArrowUp" });
  await waitFor(() => expect(POST).toHaveBeenCalledTimes(2));
  expect(body(1)).toEqual({ path: "sense.range", value: 1.1 });
});

it("a step from the live value when nothing was committed", async () => {
  const POST = setup();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <StateField sid="s1" instrument="dmm" v={RANGE} />
    </QueryClientProvider>,
  );
  const input = screen.getByLabelText("量程") as HTMLInputElement;
  input.focus();
  fireEvent.keyDown(input, { key: "ArrowUp" });
  await waitFor(() => expect(POST).toHaveBeenCalledTimes(1));
  // A continuous quantity without min/max: one whole unit per step (model.ts stepNumber).
  expect(body(0)).toEqual({ path: "sense.range", value: 1 });
});
