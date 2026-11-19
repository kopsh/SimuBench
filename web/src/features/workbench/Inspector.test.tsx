import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { toast } from "sonner";
import { afterEach, expect, it, vi } from "vitest";
import type { InstrumentInfo, StateVarInfo } from "@/api/client";
import { api } from "@/api/client";
import { TooltipProvider } from "@/components/ui/tooltip";
import { InstrumentInspector, InstrumentSettings } from "./Inspector";
import { FIELD_PAGE, globalVars, stateMap } from "./model";
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

function renderInspector(inst: InstrumentInfo = INST) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <TooltipProvider>
        <InstrumentInspector sid="s1" inst={inst} readonly={false} />
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

// Without a SCPI listener the identity says so instead of hiding the row (#1536).
it("shows an empty state when the session serves no SCPI address", () => {
  setup();
  renderInspector({ ...INST, address_virtual: null });
  const empty = screen.getByTestId("no-scpi-service");
  expect(empty.textContent).toContain("未开启 SCPI 服务");
  expect(screen.queryByText("TCPIP0::127.0.0.1::1234::SOCKET")).toBeNull();
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

// More named groups than one page of 「全部设置」 (#1582): the group a reveal points at mounts only
// after the pages flip, and must still open and mount its row — a plain field, a numbered family's
// instance or a measurement slot.
const ALPHA = "abcdefghijklmnopqrstuvwxyz";
/** ``gaa``, ``gab`` … — no trailing digits, so the groups are not joined into one numbered family. */
const groupId = (i: number) => `g${ALPHA[Math.floor(i / 26)]}${ALPHA[i % 26]}`;
const setting = (path: string, label: string): StateVarInfo =>
  ({ path, label, label_en: label, type: "float", value: 1, channel: null, min: 0, max: 10 }) as unknown as StateVarInfo;

const PLAIN_GROUPS = Array.from({ length: 50 }, (_, i) => groupId(i));
const MANY_GROUPS = {
  ...INST,
  channels: 1,
  display: { summary: [] },
  state: [
    ...PLAIN_GROUPS.flatMap((g) => Array.from({ length: 10 }, (_, k) => setting(`${g}.s${k + 1}`, `${g} 设置 ${k + 1}`))),
    // 24 numbered instances, joined into one family (#1257).
    ...Array.from({ length: 24 }, (_, n) => setting(`zzfam${n + 1}.leaf`, `编号族 ${n + 1}`)),
    // Measurement slots: one row of controls per slot.
    setting("meas.p1.kind", "测量 1 类型"),
    setting("meas.p1.source", "测量 1 来源"),
    setting("meas.p2.kind", "测量 2 类型"),
    setting("meas.p2.source", "测量 2 来源"),
  ],
} as unknown as InstrumentInfo;

it("a reveal opens a group mounted on a later page, for a field, a numbered family and a slot row (#1582)", async () => {
  setup();
  const missing = vi.spyOn(toast, "info");
  renderInspector(MANY_GROUPS);
  const shown = (path: string) => document.querySelector(`[data-setting="${path}"]`);
  const expanded = (group: string) => document.querySelector(`section[data-group="${group}"] button`)!.getAttribute("aria-expanded");

  // A plain field in the 42nd group: on the second page of groups, folded by default.
  const single = `${groupId(41)}.s3`;
  act(() => useWorkbench.getState().revealSetting("scope", single));
  await waitFor(() => expect(shown(single)).toBeTruthy());
  expect(expanded(groupId(41))).toBe("true");

  // Instance 24 of the joined numbered family.
  act(() => useWorkbench.getState().revealSetting("scope", "zzfam24.leaf"));
  const family = await screen.findByTestId("setting-family");
  await waitFor(() => expect(shown("zzfam24.leaf")).toBeTruthy());
  expect(expanded("zzfam")).toBe("true");
  expect(within(family).getByRole("combobox", { name: "zzfam 的编号" }).textContent).toBe("24");

  // A measurement slot row.
  act(() => useWorkbench.getState().revealSetting("scope", "meas.p2.source"));
  await waitFor(() => expect(shown("meas.p2.source")).toBeTruthy());
  expect(expanded("meas")).toBe("true");

  await waitFor(() => expect(useWorkbench.getState().reveal).toBeNull());
  expect(missing).not.toHaveBeenCalled();
});

// ---- a definition decides how long a list is: the inspector mounts a page at a time (#1580, #1583) ----

const fieldCount = () => document.querySelectorAll("[data-setting]").length;
/** ``aaa``, ``aab`` … — letters only, so a run of settings is not read as a numbered family. */
const letters = (i: number) => ALPHA[Math.floor(i / 676) % 26] + ALPHA[Math.floor(i / 26) % 26] + ALPHA[i % 26];
const synthetic = (state: StateVarInfo[]): InstrumentInfo =>
  ({ ...INST, channels: 1, display: { summary: [], channel_summary: [] }, state }) as unknown as InstrumentInfo;

const moreButton = () => screen.getByTestId("fields-more");
/** 「全部设置」 holds the named groups; with this many settings it starts folded, and mounts nothing until opened. */
const openAllSettings = () => act(() => screen.getByRole("button", { name: (n) => n.startsWith("全部设置") }).click());

it("two thousand loose settings mount one page, and each 'show more' adds one page (#1580, #1583)", async () => {
  setup();
  renderInspector(synthetic(Array.from({ length: 2000 }, (_, i) => setting(`s${letters(i)}`, `设置 ${letters(i)}`))));
  await waitFor(() => expect(fieldCount()).toBeGreaterThan(0));
  expect(fieldCount()).toBe(FIELD_PAGE);
  expect(moreButton().textContent).toBe("再显示 40 项（还有 1960 项）");
  act(() => moreButton().click());
  await waitFor(() => expect(fieldCount()).toBe(2 * FIELD_PAGE));
  expect(moreButton().textContent).toBe("再显示 40 项（还有 1920 项）");
});

it("the last page says how many are left, and the button goes when none are (#1580, #1583)", async () => {
  setup();
  renderInspector(synthetic(Array.from({ length: 90 }, (_, i) => setting(`s${letters(i)}`, `设置 ${letters(i)}`))));
  await waitFor(() => expect(fieldCount()).toBe(FIELD_PAGE));
  act(() => moreButton().click());
  await waitFor(() => expect(fieldCount()).toBe(2 * FIELD_PAGE));
  expect(moreButton().textContent).toBe("再显示 10 项（还有 10 项）");
  act(() => moreButton().click());
  await waitFor(() => expect(fieldCount()).toBe(90));
  expect(screen.queryByTestId("fields-more")).toBeNull();
});

it("one group of two thousand settings mounts its first page only once opened (#1580, #1583)", async () => {
  setup();
  renderInspector(synthetic(Array.from({ length: 2000 }, (_, i) => setting(`big.s${letters(i)}`, `大组 ${letters(i)}`))));
  expect(fieldCount()).toBe(0); // folded: nothing mounted for 2000 settings
  openAllSettings();
  await waitFor(() => expect(fieldCount()).toBeGreaterThan(0));
  expect(fieldCount()).toBeLessThanOrEqual(FIELD_PAGE);
  expect(moreButton().textContent).toContain("还有 1960 项");
});

it("five hundred short groups mount about a page of rows and a page of groups (#1580, #1583)", async () => {
  setup();
  renderInspector(
    synthetic(Array.from({ length: 500 }, (_, g) => Array.from({ length: 4 }, (_, k) => setting(`g${letters(g)}.s${k + 1}`, `组 ${letters(g)} ${k + 1}`))).flat()),
  );
  expect(fieldCount()).toBe(0);
  openAllSettings();
  const groups = () => document.querySelectorAll('section[data-group]:not([data-group="all"])').length;
  await waitFor(() => expect(groups()).toBeGreaterThan(0));
  expect(groups()).toBeLessThanOrEqual(FIELD_PAGE);
  expect(fieldCount()).toBeLessThanOrEqual(FIELD_PAGE);
  expect(moreButton().textContent).toContain("再显示");
});

// Build mode renders the same layout through the same component with its own field renderer.
it("build mode, with its own fields, mounts a page at a time too (#1313, #1583)", async () => {
  setup();
  const inst = synthetic(Array.from({ length: 2000 }, (_, i) => setting(`s${letters(i)}`, `设置 ${letters(i)}`)));
  render(
    <QueryClientProvider client={new QueryClient()}>
      <TooltipProvider>
        <InstrumentSettings
          sid="s1"
          inst={inst}
          readonly={false}
          stateVars={stateMap(inst)}
          globals={globalVars(inst)}
          channels={[1]}
          hidden={new Set()}
          field={(v) => <input key={v.path} aria-label={v.label ?? v.path} data-setting={v.path} />}
        />
      </TooltipProvider>
    </QueryClientProvider>,
  );
  await waitFor(() => expect(fieldCount()).toBeGreaterThan(0));
  expect(fieldCount()).toBe(FIELD_PAGE);
  act(() => moreButton().click());
  await waitFor(() => expect(fieldCount()).toBe(2 * FIELD_PAGE));
});

it("a numbered family pages its instance and starts the next instance on its first page (#1580, #1583)", async () => {
  setup();
  // Four instances: the fold threshold, as in the bookmark family above.
  const instance = (n: number) => Array.from({ length: 100 }, (_, k) => setting(`display.fam${n}.s${letters(k)}`, `实例 ${n} 项 ${letters(k)}`));
  renderInspector(synthetic([1, 2, 3, 4].flatMap(instance)));
  openAllSettings();
  const family = await screen.findByTestId("setting-family");
  expect(fieldCount()).toBeLessThanOrEqual(FIELD_PAGE);
  act(() => within(family).getByTestId("fields-more").click());
  await waitFor(() => expect(fieldCount()).toBe(2 * FIELD_PAGE));
  // Another instance of the family (a locate request picks it, as the picker does): back on its
  // first page, the first instance's pages are gone.
  act(() => useWorkbench.getState().revealSetting("scope", "display.fam2.saaa"));
  await waitFor(() => expect(within(family).getByRole("combobox", { name: "fam 的编号" }).textContent).toBe("2"));
  expect(within(family).getByLabelText("实例 2 项 aaa")).toBeTruthy();
  expect(fieldCount()).toBe(FIELD_PAGE);
});
