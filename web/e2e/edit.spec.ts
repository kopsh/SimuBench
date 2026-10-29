import { type APIRequestContext, expect, type Page, test } from "@playwright/test";

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
async function openEditor(page: Page): Promise<string> {
  const path = `e2e/edit-${Date.now()}-${counter++}.bench.yaml`;
  const put = await page.request.put("/api/benches/file", { headers, data: { path, base_sha256: null, data: BENCH } });
  expect(put.ok()).toBeTruthy();
  const start = await page.request.post("/api/sessions", { headers, data: { bench_path: path } });
  const sid = ((await start.json()) as { id: string }).id;
  await page.goto(`/s/${sid}?token=${TOKEN}`);
  await expect(page.locator(".react-flow__node")).toHaveCount(3);
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
  await page.getByRole("tab", { name: /问题/ }).click();
  await expect(page.getByText("W-IMPEDANCE-MISMATCH")).toBeVisible();
  await page.getByText("W-IMPEDANCE-MISMATCH").click();
  // The diagnostic selects the generator; its initial load setting becomes high impedance.
  const load = page.getByRole("textbox", { name: "输出负载设定" }).first();
  await load.fill("INF");
  await load.press("Enter");
  await expect(page.getByText("没有问题。")).toBeVisible();
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

test("create a bench from the home page and build it", async ({ page }) => {
  await page.goto(`/?token=${TOKEN}`);
  await page.getByRole("button", { name: "新建测试台" }).click();
  const name = `scratch_${Date.now()}`;
  await page.getByLabel("测试台名称").fill(name);
  await expect(page.getByLabel("文件路径")).toHaveValue(`${name}.bench.yaml`);
  await page.getByRole("button", { name: "创建并打开" }).click();
  await expect(page).toHaveURL(/\/s\/[0-9a-f]+$/);
  await expect(page.getByRole("tab", { name: "元件库" })).toBeVisible();

  // Drag a scope from the library onto the canvas.
  const source = page.getByTestId("library-sb-dso-4");
  const pane = page.locator(".react-flow__pane");
  await source.dragTo(pane, { targetPosition: { x: 300, y: 200 } });
  await expect(page.locator(".react-flow__node")).toHaveCount(1);
  await page.getByRole("button", { name: "添加 SB-AWG-2" }).click();
  await expect(page.locator(".react-flow__node")).toHaveCount(2);
  const doc = await readDoc(page.request, `${name}.bench.yaml`);
  expect(Object.keys(doc.data.instruments as object).sort()).toEqual(["awg", "scope"]);
});

test("review an agent's change in the version history and restore the version before it", async ({ page }) => {
  const path = await openEditor(page);
  const doc = await readDoc(page.request, path);
  // An agent edits the bench through the API (as the MCP server does).
  const ops = [{ op: "set_params", dut: "rc", params: { r: 2200 } }];
  const r = await page.request.post("/api/benches/edit", { headers, data: { path, base_sha256: doc.sha256, ops, source: "agent" } });
  expect(r.ok()).toBeTruthy();

  await page.getByRole("button", { name: "查看差异" }).click();
  const dialog = page.getByRole("dialog", { name: /版本历史/ });
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
