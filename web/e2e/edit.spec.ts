import { readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { type APIRequestContext, expect, type Page, test } from "@playwright/test";

import { expectText, isSession, sessionIdOf, settledCanvas } from "./judge";
import { WORKSPACE } from "./workspace.mjs";

const TOKEN = "e2e-token";
const headers = { "x-simubench-token": TOKEN };

const BENCH = {
  schema: "simubench.bench/2",
  name: "edit-e2e",
  instruments: {
    awg: { model: "sb-awg-2", label: "函数发生器", state: { "ch1.amplitude": 2.0 } },
    scope: { model: "sb-dso-4", label: "示波器" },
  },
  parts: { rc: { type: "rc_lowpass", label: "RC 低通", dut: true, params: { r: 1000, c: 1e-7 } } },
  wires: [
    { from: "awg.OUT1", to: "rc.IN" },
    { from: "rc.OUT", to: "scope.CH1", accessories: [{ type: "passive_probe" }] },
  ],
  layout: { awg: { x: 0, y: 80 }, rc: { x: 360, y: 100 }, scope: { x: 640, y: 40 } },
};

let counter = 0;

async function readDoc(request: APIRequestContext, path: string) {
  const r = await request.get(`/api/benches/file?path=${encodeURIComponent(path)}`, { headers });
  expect(r.ok()).toBeTruthy();
  return (await r.json()) as { sha256: string; data: Record<string, unknown>; diagnostics: { code: string }[] };
}

/** A fresh bench file and a session on it, opened in Edit mode. */
async function openEditor(page: Page, bench: Record<string, unknown> = BENCH): Promise<string> {
  const path = `e2e/edit-${Date.now()}-${counter++}.bench.yaml`;
  const put = await page.request.put("/api/benches/file", { headers, data: { path, base_sha256: null, data: bench } });
  expect(put.ok()).toBeTruthy();
  const start = await page.request.post("/api/sessions", { headers, data: { bench_path: path } });
  const sid = ((await start.json()) as { id: string }).id;
  await page.goto(`/s/${sid}?token=${TOKEN}`);
  const nodes = Object.keys(bench.instruments as object).length + Object.keys((bench.parts as object | undefined) ?? {}).length;
  await expect(page.locator(".react-flow__node")).toHaveCount(nodes);
  await page.getByRole("radio", { name: "搭建" }).click();
  await expect(page.getByRole("tab", { name: "元件库" })).toBeVisible();
  return path;
}

test("add a part from the library, undo and redo", async ({ page }) => {
  const path = await openEditor(page);
  await page.getByRole("button", { name: "添加 电阻" }).click();
  await expect(page.locator(".react-flow__node")).toHaveCount(4);
  await expect(page.getByText("已保存到文件")).toBeVisible();
  expect(Object.keys((await readDoc(page.request, path)).data.parts as object)).toEqual(["rc", "r"]);
  // A topology change applies when switching back to Operate (issue 31); nothing to click meanwhile.
  await expect(page.getByRole("button", { name: "重新启动以应用" })).toHaveCount(0);

  await page.keyboard.press("Control+z");
  await expect(page.locator(".react-flow__node")).toHaveCount(3);
  await page.getByRole("button", { name: "重做" }).click();
  await expect(page.locator(".react-flow__node")).toHaveCount(4);
});

test("connect ports by dragging, delete a wire, restart to apply", async ({ page }) => {
  const path = await openEditor(page);
  await page.getByRole("button", { name: "添加 电阻" }).click();
  const r = page.locator(".react-flow__node").filter({ hasText: "resistor" });
  await expect(r).toBeVisible();
  await page.getByRole("button", { name: "适应窗口" }).or(page.locator(".react-flow__controls-fitview")).first().click();

  const from = page.locator('.react-flow__node:has-text("函数发生器") [data-handleid="OUT2"]');
  const to = r.locator('[data-handleid="A"]');
  const a = (await from.boundingBox())!;
  const b = (await to.boundingBox())!;
  await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2);
  await page.mouse.down();
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 12 });
  await page.mouse.up();
  await expect(page.locator(".react-flow__edge")).toHaveCount(3);
  const wires = (await readDoc(page.request, path)).data.wires as { from: string; to: string }[];
  expect(wires.at(-1)).toEqual({ from: "awg.OUT2", to: "r.A" });

  // Select the new wire in the resource list and delete it with the keyboard.
  await page.getByRole("tab", { name: "资源" }).click();
  await page.getByRole("button", { name: "awg.OUT2 → r.A" }).click();
  await page.locator(".react-flow__pane").focus();
  await page.keyboard.press("Delete");
  await expect(page.locator(".react-flow__edge")).toHaveCount(2);

  // Switching back to Operate rebuilds from the new definition (issue 31).
  await page.getByRole("radio", { name: "操作" }).click();
  await expect(page.getByText("已按新的测试台定义重建")).toBeVisible();
  await expect(page.locator(".react-flow__node")).toHaveCount(4);
});

test("diagnostics guide the fix: impedance hint cleared from the inspector", async ({ page }) => {
  await openEditor(page);
  await page.getByRole("tab", { name: "问题" }).click();
  await expect(page.getByText("W-IMPEDANCE-MISMATCH")).toBeVisible();
  await page.getByText("W-IMPEDANCE-MISMATCH").click();
  // The diagnostic selects the generator; its initial load setting becomes high impedance.
  const load = page.getByRole("textbox", { name: "输出负载设定" }).first();
  await load.fill("INF");
  await load.press("Enter");
  await expect(page.getByText("没有问题。")).toBeVisible();
});

test("a second instrument at one real address is reported in the problems panel", async ({ page }) => {
  // #1597: the edit saves (the file is the user's to fix), and the address it now shares with `awg`
  // is named there — not silently accepted.
  const bench = {
    ...BENCH,
    instruments: {
      awg: { model: "sb-awg-2", label: "函数发生器", address: "TCPIP0::192.168.1.20::5025::SOCKET" },
      scope: { model: "sb-dso-4", label: "示波器", address: "TCPIP0::192.168.1.21::5025::SOCKET" },
    },
  };
  const path = await openEditor(page, bench);
  await page.locator(".react-flow__node").filter({ hasText: "示波器" }).click();
  const address = page.getByPlaceholder("TCPIP0::192.168.1.20::5025::SOCKET");
  await expect(address).toHaveValue("TCPIP0::192.168.1.21::5025::SOCKET");
  await address.fill("TCPIP0::192.168.1.20::5025::SOCKET");
  await address.press("Enter");
  await page.getByRole("tab", { name: "问题" }).click();
  // The diagnostic shows both in the problems panel and inline in the inspector (the scope is
  // still selected) — assert on the problems-panel row, which is the test's subject.
  const row = page.getByRole("button", { name: "E-ADDRESS-DUPLICATE", exact: false });
  await expect(row).toBeVisible();
  await expect(row).toContainText("与仪表 'awg' 的地址相同");
  const saved = await readDoc(page.request, path);
  expect((saved.data.instruments as Record<string, { address?: string }>).scope.address).toBe(
    "TCPIP0::192.168.1.20::5025::SOCKET",
  );
  expect(saved.diagnostics.filter((d) => d.code === "E-ADDRESS-DUPLICATE")).toHaveLength(1);
});

test("changes made elsewhere (an agent) show up live", async ({ page }) => {
  const path = await openEditor(page);
  const doc = await readDoc(page.request, path);
  const r = await page.request.post("/api/benches/edit", {
    headers,
    data: { path, base_sha256: doc.sha256, ops: [{ op: "add_part", name: "buf", type: "unity_buffer", dut: false, x: 360, y: 320 }] },
  });
  expect(r.ok()).toBeTruthy();
  await expect(page.getByText("已载入在其他地方所做的修改")).toBeVisible();
  await expect(page.locator(".react-flow__node")).toHaveCount(4);
});

test("a change made outside the service (an editor's save) shows up through the workspace stream", async ({ page }) => {
  // No API call makes this change, so no write point announces it: the workspace watcher's scan
  // notices the file on disk and the stream re-keys the page's queries (#1484) — the old 2.5 s
  // poll is gone, this can only arrive as an event.
  const path = await openEditor(page);
  const file = join(WORKSPACE, path);
  const raw = readFileSync(file, "utf8");
  writeFileSync(file, raw.replace("label: RC 低通", "label: RC 低通（外部改）"), "utf8");
  await expect(page.locator(".react-flow__node").filter({ hasText: "RC 低通（外部改）" })).toBeVisible();
});


test("create a bench from the home page and build it", async ({ page }) => {
  await page.goto(`/?token=${TOKEN}`);
  await page.getByRole("button", { name: "新建测试台" }).click();
  const name = `scratch_${Date.now()}`;
  await page.getByLabel("测试台名称").fill(name);
  await expect(page.getByLabel("文件路径")).toHaveValue(`${name}.bench.yaml`);
  await page.getByRole("button", { name: "创建并打开" }).click();
  await expect(page).toHaveURL(isSession);
  await expect(page.getByRole("tab", { name: "元件库" })).toBeVisible();
  // The library groups by class, as the home page does (#1356).
  const scopes = page.getByRole("heading", { name: "示波器", exact: true });
  await expect(scopes).toBeVisible();
  await expect(scopes.locator("xpath=..").getByTestId("library-sb-dso-4")).toBeVisible();

  // Drag a scope from the library onto the canvas.
  const source = page.getByTestId("library-sb-dso-4");
  const pane = page.locator(".react-flow__pane");
  await source.dragTo(pane, { targetPosition: { x: 300, y: 200 } });
  await expect(page.locator(".react-flow__node")).toHaveCount(1);
  await page.getByRole("button", { name: "添加 SB-AWG-2" }).click();
  await expect(page.locator(".react-flow__node")).toHaveCount(2);
  const doc = await readDoc(page.request, `${name}.bench.yaml`);
  expect(Object.keys(doc.data.instruments as object).sort()).toEqual(["awg", "scope"]);
  // The build inspector says what its fields change, not "switch to Build" (#1356).
  await page.locator(".react-flow__node").filter({ hasText: "scope" }).first().click();
  await expect(page.getByText("这里改的是打开测试台时的初始设置")).toBeVisible();
  await expect(page.getByText("要修改请切换到搭建")).toHaveCount(0);
});

test("review an agent's change in the version history and restore the version before it", async ({ page }) => {
  const path = await openEditor(page);
  const doc = await readDoc(page.request, path);
  // An agent edits the bench through the API (as the MCP server does).
  const ops = [{ op: "set_params", dut: "rc", params: { r: 2200 } }];
  const r = await page.request.post("/api/benches/edit", { headers, data: { path, base_sha256: doc.sha256, ops, source: "agent" } });
  expect(r.ok()).toBeTruthy();

  await page.getByRole("button", { name: "查看差异" }).click();
  const dialog = page.getByRole("dialog", { name: "版本历史" });
  const versions = dialog.getByRole("list", { name: "版本" });
  await expect(versions.getByRole("button")).toHaveCount(2);
  await expect(versions.getByRole("button").first()).toContainText("Agent");
  const diff = dialog.getByTestId("history-diff");
  await expect(diff).toContainText("params.r");
  await expect(diff).toContainText("1000");
  await expect(diff).toContainText("2200");

  await versions.getByRole("button").nth(1).click();
  await dialog.getByRole("button", { name: "恢复此版本" }).click();
  await expect(versions.getByRole("button")).toHaveCount(3);
  await expect(versions.getByRole("button").first()).toContainText("恢复");
  expect(((await readDoc(page.request, path)).data.parts as { rc: { params: { r: number } } }).rc.params.r).toBe(1000);
});

test("individual deviations: an instrument draws from its specs, the drawn values stay hidden", async ({ page }) => {
  const path = await openEditor(page);
  await page.locator(".react-flow__node").filter({ hasText: "函数发生器" }).click();
  await page.getByRole("button", { name: "个体偏差" }).click();
  const ranges = page.getByRole("definition").filter({ hasText: "±2 %" });
  await expect(ranges).toHaveCount(2); // one output impedance per channel
  await page.getByRole("switch", { name: "按指标抽取" }).click();
  await expect.poll(async () => (await readDoc(page.request, path)).data.instruments).toMatchObject({
    awg: { deviation: "from_specs" },
  });
  // Back in Operate mode the bench is rebuilt: values are drawn, but true values may not be read.
  await page.getByRole("radio", { name: "操作" }).click();
  await page.locator(".react-flow__node").filter({ hasText: "函数发生器" }).click();
  await page.getByRole("radio", { name: "搭建" }).click();
  await page.locator(".react-flow__node").filter({ hasText: "函数发生器" }).click();
  await page.getByRole("button", { name: "个体偏差" }).click();
  await expect(page.getByText("已隐藏")).toHaveCount(2);
});

test("options: install the preamplifier from the inspector, the bench file lists it (#1007)", async ({ page }) => {
  const path = await openEditor(page, {
    schema: "simubench.bench/2",
    name: "options-e2e",
    instruments: { sa: { model: "keysight-n9320b", label: "频谱仪" }, scope: { model: "sb-dso-4", label: "示波器" } },
  });
  // A model without options shows no options section.
  await page.locator(".react-flow__node").filter({ hasText: "示波器" }).click();
  await expect(page.getByRole("switch", { name: "装上选件 P03" })).toHaveCount(0);
  await page.locator(".react-flow__node").filter({ hasText: "频谱仪" }).click();
  const p03 = page.getByRole("switch", { name: "装上选件 P03" });
  await expect(p03).not.toBeChecked();
  await p03.click();
  await expect.poll(async () => (await readDoc(page.request, path)).data.instruments).toMatchObject({ sa: { options: ["P03"] } });
  await expect(p03).toBeChecked();
  await p03.click();
  await expect.poll(async () => ((await readDoc(page.request, path)).data.instruments as { sa: { options?: string[] } }).sa.options ?? []).toEqual([]);
});

test("DUT is a role: mark the meter under calibration, not only a part", async ({ page }) => {
  const path = await openEditor(page);
  await expect(page.locator(".react-flow__node").filter({ hasText: "RC 低通" }).getByTestId("dut-badge")).toBeVisible();
  const scope = page.locator(".react-flow__node").filter({ hasText: "示波器" });
  await expect(scope.getByTestId("dut-badge")).toHaveCount(0);
  await scope.click();
  await page.getByRole("switch", { name: "DUT" }).click();
  await expect.poll(async () => (await readDoc(page.request, path)).data.instruments).toMatchObject({ scope: { dut: true } });
  await expect(scope.getByTestId("dut-badge")).toBeVisible();
  // The RC network is a fixture now, not the object under test.
  await page.locator(".react-flow__node").filter({ hasText: "RC 低通" }).click();
  await page.getByRole("switch", { name: "DUT" }).click();
  // false is the default, so the file leaves it out
  await expect.poll(async () => ((await readDoc(page.request, path)).data.parts as { rc: { dut?: boolean } }).rc.dut ?? false).toBe(false);
  await expect(page.locator(".react-flow__node").filter({ hasText: "RC 低通" })).toContainText("元件 · rc_lowpass");
  // Back in Operate mode the resource tree marks the DUT too.
  await page.getByRole("radio", { name: "操作" }).click();
  const tree = page.getByRole("navigation", { name: "资源" });
  await expect(tree.getByTestId("dut-badge")).toHaveCount(1);
  await expect(tree.getByRole("button").filter({ hasText: "示波器" }).getByTestId("dut-badge")).toBeVisible();
});

test("build mode: the canvas shows the initial settings, a screen the running values and what differs", async ({ page }) => {
  await openEditor(page);
  const sid = new URL(page.url()).pathname.split("/").pop();
  await expect(page.getByRole("note", { name: "初始设置" })).toBeVisible();
  // A script turns CH1's output on while the bench is being built.
  const r = await page.request.post(`/api/sessions/${sid}/instruments/awg/scpi`, { headers, data: { command: ":OUTP1 ON" } });
  expect(r.ok()).toBeTruthy();
  const awg = page.locator(".react-flow__node").filter({ hasText: "函数发生器" });
  await expect(awg).toContainText("关"); // the bench file: output off
  await awg.dblclick();
  const banner = page.getByRole("note", { name: "运行中的值" });
  await expect(banner).toBeVisible();
  await expect(banner).toContainText("CH1 输出：开（初始：关）");
  // Operate mode shows the running bench everywhere: no notes.
  await page.getByRole("radio", { name: "操作" }).click();
  await expect(page.getByRole("note", { name: "运行中的值" })).toHaveCount(0);
  await page.getByRole("tab", { name: "画布" }).click();
  await expect(page.getByRole("note", { name: "初始设置" })).toHaveCount(0);
});

test("copy as my instrument stays in the bench: the tab turns into the copy, the bench may use it, one copy only (#1250)", async ({ page }) => {
  const model = "siglent-sds2104xplus";
  const path = `e2e/copy-${Date.now()}.bench.yaml`;
  const bench = { schema: "simubench.bench/2", name: "copy-e2e", instruments: { scope: { model, label: "示波器" } }, layout: { scope: { x: 0, y: 0 } } };
  expect((await page.request.put("/api/benches/file", { headers, data: { path, base_sha256: null, data: bench } })).ok()).toBeTruthy();
  const start = await page.request.post("/api/sessions", { headers, data: { bench_path: path } });
  const sid = ((await start.json()) as { id: string }).id;
  let copy = "";
  try {
    await page.goto(`/s/${sid}?token=${TOKEN}`);
    await page.locator(".react-flow__node").filter({ hasText: "示波器" }).click();
    await page.getByRole("button", { name: "验收页" }).click();
    const tab = page.getByRole("tab", { name: "示波器 · 验收" });
    await expect(tab).toHaveAttribute("aria-selected", "true");

    // Copied from 「编辑结构」: the same tab shows the copy, still at 「编辑结构」, and it can be edited.
    await page.getByTestId("acceptance-tab-build").click();
    await page.getByRole("button", { name: "复制为我的仪表" }).click();
    await expect(page.getByTestId("build-library")).toBeVisible();
    await expect(page).toHaveURL(isSession);
    await expect(tab).toHaveAttribute("aria-selected", "true");
    await expect(page.getByTestId("acceptance-tab-build")).toHaveAttribute("aria-selected", "true");
    const catalog = (await (await page.request.get("/api/catalog", { headers })).json()) as { instruments: { id: string; workspace: boolean; extends: string | null }[] };
    copy = catalog.instruments.find((d) => d.workspace && d.extends === model)?.id ?? "";
    expect(copy.startsWith(`my-${model}`)).toBe(true);

    // Asked at once whether the scope should use the copy; yes writes it into the bench file, undo takes it back.
    await expect(page.getByText("让 scope 改用副本？")).toBeVisible();
    await page.getByRole("button", { name: "改用副本" }).click();
    const scopeModel = async () => ((await readDoc(page.request, path)).data.instruments as Record<string, { model: string }>).scope.model;
    await expect.poll(scopeModel).toBe(copy);
    await expect(page.getByText(`scope 已改用 ${copy}`)).toBeVisible();
    await page.getByRole("button", { name: "撤销", exact: true }).click();
    await expect.poll(scopeModel).toBe(model);

    // Its own page, opened from the bench, leads back to the bench.
    await page.getByTestId("acceptance-own-page").click();
    await expect(page).toHaveURL((url) => url.pathname === `/i/${copy}` && url.searchParams.get("from") === sid);
    await expect(page.getByTestId("back-to-bench")).toContainText("copy-e2e");

    // The built-in model now offers the copy instead of a second one.
    await page.goto(`/i/${model}?from=${sid}&section=build&token=${TOKEN}`);
    await expect(page.getByTestId("acceptance-tab-build")).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("button", { name: "复制为我的仪表" })).toHaveCount(0);
    await page.getByRole("button", { name: "打开我的副本" }).click();
    await expect(page).toHaveURL((url) => url.pathname === `/i/${copy}`);
    await expect(page.getByTestId("acceptance-tab-build")).toHaveAttribute("aria-selected", "true");
    const after = (await (await page.request.get("/api/catalog", { headers })).json()) as { instruments: { workspace: boolean; extends: string | null }[] };
    expect(after.instruments.filter((d) => d.workspace && d.extends === model)).toHaveLength(1);
    await page.getByTestId("back-to-bench").click();
    await expect(page).toHaveURL((url) => sessionIdOf(url) === sid);

    // Build mode: the copy is marked as mine with its own id; the model picker leads with the model.
    await page.getByRole("radio", { name: "搭建" }).click();
    await page.getByRole("tab", { name: "元件库" }).click();
    const mine = page.getByTestId(`library-${copy}`);
    await expect(mine).toContainText("我的");
    await expect(mine).toContainText(copy);
    await expect(page.getByTestId(`library-${model}`)).not.toContainText("我的");
    await page.locator(".react-flow__node").filter({ hasText: "示波器" }).click();
    await expectText(page.getByTestId("edit-model"), (text) => text.trim().startsWith("SDS2104X Plus"), "the model before its maker");
    await page.getByTestId("edit-model").click();
    await expect(page.getByRole("option", { name: "SDS2104X Plus 我的" })).toContainText(copy);
  } finally {
    // Other specs open this model by name: leave no copy behind (#1216).
    if (copy) for (const suffix of [".yaml", ".layout.json"]) rmSync(join(WORKSPACE, "instruments", `${copy}${suffix}`), { force: true });
  }
});

test("an instrument added from the library lands beside the bench's nodes, not on them (#1258)", async ({ page }) => {
  // The Hello World bench's layout: the generator on the left, the scope and the analyzer stacked on the right.
  const hello = {
    schema: "simubench.bench/2",
    name: "hello-place",
    instruments: {
      awg: { model: "siglent-sdg2042x", label: "函数发生器" },
      scope: { model: "keysight-uxr1104b", label: "示波器" },
      sa: { model: "keysight-n9320b", label: "频谱分析仪" },
    },
    wires: [
      { from: "awg.CH1", to: "scope.CH1" },
      { from: "awg.CH2", to: "sa.RF_IN" },
    ],
    layout: { awg: { x: 0, y: 120 }, scope: { x: 420, y: 0 }, sa: { x: 420, y: 240 } },
  };
  const path = await openEditor(page, hello);
  await page.getByRole("button", { name: "添加 34465A" }).click();
  const nodes = page.locator(".react-flow__node");
  await expect(nodes).toHaveCount(4);
  await expect(page.getByText("已保存到文件")).toBeVisible();
  const boxes = await nodes.evaluateAll((els) =>
    els.map((e) => {
      const r = e.getBoundingClientRect();
      return { id: e.getAttribute("data-id"), r: [r.left, r.top, r.right, r.bottom] };
    }),
  );
  const overlap = (a: number[], b: number[]) => a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3];
  for (const a of boxes) for (const b of boxes) if (a !== b) expect(overlap(a.r, b.r), `${a.id} and ${b.id} overlap`).toBe(false);
  const layout = (await readDoc(page.request, path)).data.layout as Record<string, { x: number; y: number }>;
  expect(Object.keys(layout).sort()).toEqual(["awg", "dmm", "sa", "scope"]);
});

test("wiring two outputs together is refused before the drop, the bench's wires and file stay (#1269)", async ({ page }) => {
  const hello = {
    schema: "simubench.bench/2",
    name: "hello-drivers",
    instruments: {
      awg: { model: "siglent-sdg2042x", label: "函数发生器" },
      scope: { model: "keysight-uxr1104b", label: "示波器" },
      sa: { model: "keysight-n9320b", label: "频谱分析仪" },
    },
    wires: [
      { from: "awg.CH1", to: "scope.CH1" },
      { from: "awg.CH2", to: "sa.RF_IN" },
    ],
    layout: { awg: { x: 0, y: 120 }, scope: { x: 420, y: 0 }, sa: { x: 420, y: 240 } },
  };
  const path = await openEditor(page, hello);
  const before = (await readDoc(page.request, path)).sha256;
  await page.locator(".react-flow__controls-fitview").click();
  // the bottom panel open, as in the review's scenario
  await page.getByRole("tab", { name: "问题" }).click();
  // The panel open resizes and refits the canvas, and the fonts landing refit it once too
  // (#645/#1354): measure the handles only after the view has stopped moving (#1669).
  await settledCanvas(page);

  const from = page.locator('.react-flow__node[data-id="scope"] [data-handleid="CAL_OUT"]');
  const to = page.locator('.react-flow__node[data-id="awg"] [data-handleid="CH1"]');
  const a = (await from.boundingBox())!;
  const b = (await to.boundingBox())!;
  await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2);
  await page.mouse.down();
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 12 });
  // before the drop: why not, with both ports named
  const hint = page.getByTestId("connect-hint");
  await expect(hint).toBeVisible();
  await expect(hint).toContainText("scope.CAL_OUT");
  await expect(hint).toContainText("awg.CH1");
  await page.mouse.up();
  await expect(hint).toHaveCount(0);

  // both wires are still drawn, nothing is left out, and the file did not change
  await expect(page.locator(".react-flow__edge")).toHaveCount(2);
  await expect(page.locator('.react-flow__edge[data-id="wire-0"]')).toBeVisible();
  await expect(page.getByText("因错误未显示")).toHaveCount(0);
  expect((await readDoc(page.request, path)).sha256).toBe(before);

  // the minimap, if shown, covers no node
  const overlap = (p: number[], q: number[]) => p[0] < q[2] && q[0] < p[2] && p[1] < q[3] && q[1] < p[3];
  const rect = (r: { x: number; y: number; width: number; height: number }) => [r.x, r.y, r.x + r.width, r.y + r.height];
  const map = page.locator(".react-flow__minimap");
  if (await map.count()) {
    const m = rect((await map.boundingBox())!);
    for (const n of await page.locator(".react-flow__node").all()) expect(overlap(m, rect((await n.boundingBox())!))).toBe(false);
  }

  // the same output to a free input is still a wire: CAL_OUT to the scope's own CH2
  const c = (await page.locator('.react-flow__node[data-id="scope"] [data-handleid="CH2"]').boundingBox())!;
  const a2 = (await from.boundingBox())!;
  await page.mouse.move(a2.x + a2.width / 2, a2.y + a2.height / 2);
  await page.mouse.down();
  await page.mouse.move(c.x + c.width / 2, c.y + c.height / 2, { steps: 12 });
  await expect(page.getByTestId("connect-hint")).toHaveCount(0);
  await page.mouse.up();
  await expect(page.locator(".react-flow__edge")).toHaveCount(3);
});
