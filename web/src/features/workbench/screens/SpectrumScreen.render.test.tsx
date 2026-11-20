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
  HTMLCanvasElement.prototype.getContext = (() => null) as unknown as typeof HTMLCanvasElement.prototype.getContext;
});

const hooks = vi.hoisted(() => ({
  clock: vi.fn(),
  scpi: vi.fn(),
  continuous: true,
}));
vi.mock("@/api/hooks", () => ({
  scpiOutcome: () => ({ ok: true, message: "" }),
  useScpi: () => ({ mutate: hooks.scpi, isPending: false }),
  useClockMode: () => ({ mutate: hooks.clock, isPending: false }),
  useInstrumentReadings: () => ({
    values: { "sa.sweep": 3, "sa.sweep_time": 0.25 },
    query: { data: { traces: [] } },
    vars: new Map([["init.continuous", { path: "init.continuous", value: hooks.continuous }]]),
  }),
}));

vi.mock("../StateField", () => ({ StateField: () => null }));
vi.mock("./parts", () => ({ ScreenIdentity: () => null }));

afterEach(() => {
  cleanup();
  hooks.clock.mockClear();
  hooks.scpi.mockClear();
  hooks.continuous = true;
});

const inst = { name: "sa", display: {}, state: [] } as unknown as InstrumentInfo;
const show = (clockMode: string, readonly = false) =>
  render(
    <TooltipProvider>
      <SpectrumScreen sid="s" inst={inst} readonly={readonly} clockMode={clockMode} />
    </TooltipProvider>,
  );
const sent = () => hooks.scpi.mock.calls.map((c) => c[0]);
const pressed = (id: string) => screen.getByTestId(id).getAttribute("aria-pressed");

// #1666: the analyzer's Cont and Single keys replace the hint bar; the lit one is the mode.
it("shows the sweep keys with Continuous lit, and no hint bar or HOLD while sweeping", () => {
  show("command");
  expect(screen.queryByTestId("sweep-hint")).toBeNull();
  expect(screen.queryByTestId("hold")).toBeNull();
  expect([pressed("sweep-continuous"), pressed("sweep-single")]).toEqual(["true", "false"]);
});

it("a single sweep lights Single and shows HOLD with its explanation", () => {
  hooks.continuous = false;
  show("wall");
  expect([pressed("sweep-continuous"), pressed("sweep-single")]).toEqual(["false", "true"]);
  expect(screen.getByTestId("hold").textContent).toBe("HOLD");
  expect(screen.getByTestId("hold").getAttribute("title")).toContain("单次扫描");
});

it("Single turns continuous sweep off and sweeps once, whatever the clock", () => {
  show("command");
  fireEvent.click(screen.getByTestId("sweep-single"));
  expect(sent()).toEqual([":INIT:CONT OFF;:INIT:IMM"]);
  expect(hooks.clock).not.toHaveBeenCalled();
});

// On a paused clock nothing would move: Continuous from the screen lets the clock run too.
it("Continuous on a paused clock sweeps and switches the clock live", () => {
  hooks.continuous = false;
  show("command");
  fireEvent.click(screen.getByTestId("sweep-continuous"));
  expect(sent()).toEqual([":INIT:CONT ON"]);
  expect(hooks.clock).toHaveBeenCalledTimes(1);
  expect(hooks.clock.mock.calls[0][0]).toEqual({ mode: "wall" });
});

it("Continuous on a live clock leaves the clock alone", () => {
  hooks.continuous = false;
  show("wall");
  fireEvent.click(screen.getByTestId("sweep-continuous"));
  expect(sent()).toEqual([":INIT:CONT ON"]);
  expect(hooks.clock).not.toHaveBeenCalled();
});

it("read-only keeps the keys but disables them", () => {
  show("command", true);
  expect((screen.getByTestId("sweep-continuous") as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByTestId("sweep-single") as HTMLButtonElement).disabled).toBe(true);
});
