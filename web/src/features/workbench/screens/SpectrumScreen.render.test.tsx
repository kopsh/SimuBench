import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { InstrumentInfo } from "@/api/client";
import { TooltipProvider } from "@/components/ui/tooltip";
import "@/i18n";
import { SpectrumScreen } from "./SpectrumScreen";

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
  HTMLCanvasElement.prototype.getContext = (() =>
    null) as unknown as typeof HTMLCanvasElement.prototype.getContext;
});

const hooks = vi.hoisted(() => ({
  advance: vi.fn(),
  clock: vi.fn(),
  scpi: vi.fn(),
  continuous: true,
}));
vi.mock("@/api/hooks", () => ({
  scpiOutcome: () => ({ ok: true, message: "" }),
  useAdvance: () => ({ mutate: hooks.advance, isPending: false }),
  useScpi: () => ({ mutate: hooks.scpi, isPending: false }),
  useClockMode: () => ({ mutate: hooks.clock, isPending: false }),
  useInstrumentReadings: () => ({
    values: { "sa.sweep": 3, "sa.sweep_time": 0.25 },
    query: { data: { traces: [] } },
    vars: new Map([["init.continuous", { value: hooks.continuous }]]),
  }),
}));

vi.mock("../StateField", () => ({ StateField: () => null }));
vi.mock("./parts", () => ({ ScreenIdentity: () => null }));

afterEach(() => {
  cleanup();
  hooks.advance.mockClear();
  hooks.clock.mockClear();
  hooks.scpi.mockClear();
  hooks.continuous = true;
});

const inst = {
  name: "sa",
  display: {},
  state: [],
} as unknown as InstrumentInfo;
const show = (clockMode: string, readonly = false) =>
  render(
    <TooltipProvider>
      <SpectrumScreen
        sid="s"
        inst={inst}
        readonly={readonly}
        clockMode={clockMode}
      />
    </TooltipProvider>,
  );

// #1178: the hint's appearance conditions, not only sweepStatus().
it("a command clock explains the standing trace and its button advances one sweep time", () => {
  show("command");
  expect(screen.getByTestId("sweep-hint").textContent).toContain("命令时钟");
  expect(screen.queryByTestId("hold")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "推进一次扫描时间" }));
  expect(hooks.advance).toHaveBeenCalledTimes(1);
  expect(hooks.advance.mock.calls[0][0]).toBe(0.25);
});

// #1660: on the command clock the hint also offers the live clock, which sweeps by itself.
it("a command clock's live-sweep button switches the clock to the wall clock", () => {
  show("command");
  fireEvent.click(screen.getByTestId("sweep-live"));
  expect(hooks.clock).toHaveBeenCalledTimes(1);
  expect(hooks.clock.mock.calls[0][0]).toEqual({ mode: "wall" });
});

it("read-only hides the advance button but keeps the hint", () => {
  show("command", true);
  expect(screen.getByTestId("sweep-hint")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "推进一次扫描时间" })).toBeNull();
  expect(screen.queryByTestId("sweep-live")).toBeNull();
});

it("a single sweep shows HOLD and the single-sweep hint without the advance button", () => {
  hooks.continuous = false;
  show("command");
  expect(screen.getByTestId("hold").textContent).toBe("HOLD");
  expect(screen.getByTestId("sweep-hint").textContent).toContain("单次扫描");
  expect(screen.queryByRole("button", { name: "推进一次扫描时间" })).toBeNull();
});

// #1660: in single sweep the hint holds the analyzer's own Single and Cont keys.
it("a single sweep's buttons sweep once and turn continuous sweep back on", () => {
  hooks.continuous = false;
  show("wall");
  fireEvent.click(screen.getByTestId("sweep-single"));
  fireEvent.click(screen.getByTestId("sweep-continuous"));
  expect(hooks.scpi.mock.calls.map((c) => c[0])).toEqual([
    ":INIT:IMM",
    ":INIT:CONT ON",
  ]);
  expect(screen.queryByTestId("sweep-live")).toBeNull();
});

it("a wall clock with continuous sweeping shows no hint and no HOLD", () => {
  show("wall");
  expect(screen.queryByTestId("sweep-hint")).toBeNull();
  expect(screen.queryByTestId("hold")).toBeNull();
});
