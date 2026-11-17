import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { InstrumentInfo, ScopeScreen as ScopeScreenData, SessionInfo } from "@/api/client";
import { TooltipProvider } from "@/components/ui/tooltip";
import "@/i18n";
import { ScopeScreen, scopeAxisChannel } from "./ScopeScreen";

vi.hoisted(() => {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  })) as unknown as typeof window.matchMedia;
  // The trace canvas observes its size and draws; jsdom has neither.
  window.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
  HTMLCanvasElement.prototype.getContext = (() => null) as unknown as typeof HTMLCanvasElement.prototype.getContext;
});

const data = vi.hoisted(() => ({ screen: undefined as unknown }));
vi.mock("@/api/hooks", () => ({
  useScopeScreen: () => ({ data: data.screen, isLoading: false }),
  usePanel: () => ({ mutate: vi.fn(), isPending: false }),
}));

afterEach(cleanup);

const trace = { channel: 1, t0_s: -5e-6, dt_s: 5e-10, points: 2, volts: [0, 0.1], scale: 0.1, offset: 0, triggered: true, silence: [], minmax: null };
const base: ScopeScreenData = {
  instrument: "scope",
  source: "record",
  running: false,
  waiting: false,
  timebase_scale: 1e-6,
  screen_left_s: -5e-6,
  trigger_level: 0,
  trigger_source: 1,
  traces: [trace],
  functions: [],
  acquisitions: 1,
  measurements: [],
};
const fft = {
  function: 1,
  operator: "FFTMagnitude",
  spectrum: true,
  x0: 0,
  dx: 122070.3125,
  points: 3,
  unit: "DECibels",
  values: [-60, -2.1, -60],
  scale: 10,
  offset: -40,
  minmax: null,
};
const inst = { name: "scope", channels: 4, state: [] } as unknown as InstrumentInfo;
const session = { id: "s" } as unknown as SessionInfo;
const show = (screenData: ScopeScreenData, clockMode = "wall") => {
  data.screen = screenData;
  return render(
    <TooltipProvider>
      <ScopeScreen session={{ ...session, clock_mode: clockMode } as SessionInfo} inst={inst} readonly={false} />
    </TooltipProvider>,
  );
};

it("draws no spectrum pane while no transform is turned on", () => {
  show(base);
  expect(screen.queryByTestId("scope-spectrum")).toBeNull();
  expect(screen.getAllByRole("img", { name: "waveform" })).toHaveLength(1);
});

it("draws a turned-on FFT in its own pane with its scale, span and bin width", () => {
  show({ ...base, functions: [fft] });
  const pane = screen.getByTestId("scope-spectrum");
  expect(pane.textContent).toContain("F1");
  expect(pane.textContent).toContain("FFTMagnitude");
  expect(pane.textContent).toContain("10 dB/div");
  expect(pane.textContent).toContain("244.1 kHz"); // the last of the three bins
  expect(screen.getByRole("img", { name: "频谱" })).toBeTruthy();
});

it("draws a time-domain function over the channels, not in the spectrum pane", () => {
  show({ ...base, functions: [{ ...fft, function: 2, operator: "ADD", spectrum: false, unit: "VOLT" }] });
  expect(screen.queryByTestId("scope-spectrum")).toBeNull();
});

it("says a live frame takes no virtual time on a command or stepped clock, and only then (#1334)", () => {
  const live = { ...base, source: "live", running: true } as ScopeScreenData;
  show(live, "command");
  expect(screen.getByTestId("scope-clock-hint").textContent).toContain("屏幕帧不占用虚拟时间");
  cleanup();
  show(live, "stepped");
  expect(screen.getByTestId("scope-clock-hint")).toBeTruthy();
  cleanup();
  show(live, "wall");
  expect(screen.queryByTestId("scope-clock-hint")).toBeNull();
  cleanup();
  show(base, "command"); // stopped on a record: no live frames to explain
  expect(screen.queryByTestId("scope-clock-hint")).toBeNull();
});

it("uses the cursor channel for the voltage axis when it is displayed", () => {
  expect(scopeAxisChannel([1, 2], 2, 1)).toBe(2);
});

it("falls back to a displayed trigger channel when the cursor channel is hidden", () => {
  expect(scopeAxisChannel([1, 2], 3, 2)).toBe(2);
});

it("falls back to the first displayed channel when cursor and trigger channels are hidden", () => {
  expect(scopeAxisChannel([1, 2], 3, 4)).toBe(1);
  expect(scopeAxisChannel([], undefined, undefined)).toBeUndefined();
});
