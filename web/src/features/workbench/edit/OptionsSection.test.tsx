import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { InstrumentInfo } from "@/api/client";
import "@/i18n";
import { OptionsSection } from "./EditInspector";
import { applyOps } from "./editor";

// The inspector's module graph reads the colour-scheme preference at import; jsdom has no matchMedia.
vi.hoisted(() => {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  })) as unknown as typeof window.matchMedia;
});
vi.mock("./editor", () => ({ applyOps: vi.fn(() => Promise.resolve(undefined)) }));

afterEach(() => {
  cleanup();
  vi.mocked(applyOps).mockClear();
});

const inst = (installed: string[]) =>
  ({
    name: "sa",
    options: ["P03", "B25"].map((id) => ({ id, label: null, params: [], commands: [], installed: installed.includes(id) })),
  }) as unknown as InstrumentInfo;

// D68, #1007: a switch per declared option; a change replaces the instance's list through set_options.
it("a switch installs or removes one option and keeps the others", () => {
  render(<OptionsSection inst={inst(["B25"])} />);
  fireEvent.click(screen.getByRole("switch", { name: "装上选件 P03" }));
  expect(applyOps).toHaveBeenLastCalledWith([{ op: "set_options", instrument: "sa", options: ["B25", "P03"] }]);
  fireEvent.click(screen.getByRole("switch", { name: "装上选件 B25" }));
  expect(applyOps).toHaveBeenLastCalledWith([{ op: "set_options", instrument: "sa", options: [] }]);
});
