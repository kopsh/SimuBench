import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { InstrumentInfo, StateVarInfo } from "@/api/client";
import { api } from "@/api/client";
import { TooltipProvider } from "@/components/ui/tooltip";
import { InstrumentInspector } from "./Inspector";
import { useWorkbench } from "./store";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  useWorkbench.setState({ reveal: null });
});

// Four numbered instances (the fold threshold) of a display family beside a plain setting, as
// keysight-uxr1104b carries display.bookmark1..100 (#1257) — small enough to render in jsdom.
const LEAVES: [string, StateVarInfo["type"], string[] | undefined][] = [
  ["color", "enum", ["", "CHANnel1", "CHANnel2", "GRID"]],
  ["source", "enum", ["NONE", "CHANnel1", "CHANnel2"]],
  ["defined", "bool", undefined],
  ["label", "string", undefined],
  ["content", "string", undefined],
];
const bookmark = (n: number): StateVarInfo[] =>
  LEAVES.map(([leaf, type, options]) => ({
    path: `display.bookmark${n}.${leaf}`,
    label: `书签 ${n} ${leaf}`,
    label_en: `Bookmark ${n} ${leaf}`,
    type,
    value: type === "bool" ? false : "",
    channel: null,
    options,
  })) as unknown as StateVarInfo[];

const INST = {
  name: "scope",
  label: "示波器",
  model: "UXR1104B",
  manufacturer: "Keysight Technologies",
  idn: "Keysight Technologies,UXR1104B,MY0,1.0",
  address_virtual: "TCPIP0::127.0.0.1::1234::SOCKET",
  errors: [],
  remote: [],
  channels: 2,
  display: { summary: [] },
  state: [
    ...[1, 2, 3, 4].flatMap(bookmark),
    { path: "display.graticule", label: "网格类型", type: "enum", value: "GRID", channel: null, options: ["GRID", "FRAMe"] },
  ],
} as unknown as InstrumentInfo;

function setup() {
  return vi.spyOn(api, "GET").mockResolvedValue({
    data: { id: "s1", bench_path: "benches/keysight-uxr1104b.bench.yaml", clients: [], instruments: [] },
    error: undefined,
    response: new Response(),
  } as never);
}

function renderInspector() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <TooltipProvider>
        <InstrumentInspector sid="s1" inst={INST} readonly={false} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

// The numbered family folds into one block with an instance picker; the plain setting stays a row.
it("a numbered family folds into one block with a picker, a few settings stay rows (#1286)", async () => {
  setup();
  renderInspector();
  const family = await screen.findByTestId("setting-family");
  expect(family.getAttribute("data-family")).toBe("display.bookmark");
  // The picker starts at the first instance and says how many there are.
  expect(within(family).getByRole("combobox", { name: "bookmark 的编号" }).textContent).toBe("1");
  expect(family.textContent).toContain("共 4 个");
  expect(within(family).getByLabelText("书签 1 color")).toBeTruthy();
  expect(within(family).queryByLabelText("书签 2 color")).toBeNull();
  // A setting outside the family is a row beside the block, not inside it.
  expect(screen.getByLabelText("网格类型")).toBeTruthy();
});

// "在检查器中定位" (#800) for a setting inside a family: the block switches to that instance.
it("a locate request for a family's setting picks that instance (#1286)", async () => {
  setup();
  renderInspector();
  act(() => useWorkbench.getState().revealSetting("scope", "display.bookmark3.color"));
  const family = await screen.findByTestId("setting-family");
  await waitFor(() => expect(within(family).getByRole("combobox", { name: "bookmark 的编号" }).textContent).toBe("3"));
  expect(within(family).getByLabelText("书签 3 color")).toBeTruthy();
  expect(within(family).queryByLabelText("书签 1 color")).toBeNull();
});

// The settings come first (#1355): the identity folds to the script's address; opened, *IDN? and the rest.
it("the identity section starts folded to the virtual address and opens on demand", async () => {
  setup();
  localStorage.removeItem("simubench:inspector-identity");
  renderInspector();
  const section = screen.getByTestId("identity-section");
  expect(within(section).getByText("TCPIP0::127.0.0.1::1234::SOCKET")).toBeTruthy();
  expect(within(section).queryByText("Keysight Technologies,UXR1104B,MY0,1.0")).toBeNull();
  act(() => within(section).getByRole("button", { name: (n) => n.startsWith("身份与地址") }).click());
  expect(within(section).getByText("Keysight Technologies,UXR1104B,MY0,1.0")).toBeTruthy();
  expect(localStorage.getItem("simubench:inspector-identity")).toBe("open");
});

it("beside its own screen, opening the screen is a secondary action in the row", () => {
  setup();
  useWorkbench.setState({ active: "screen:scope" });
  renderInspector();
  const actions = screen.getByTestId("instrument-actions");
  const open = within(actions).getByRole("button", { name: "打开屏幕" });
  expect(open.getAttribute("data-variant")).toBe("outline");
  useWorkbench.setState({ active: "canvas" });
});
