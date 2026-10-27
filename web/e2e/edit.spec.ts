import { type APIRequestContext, expect, type Page, test } from "@playwright/test";

const TOKEN = "e2e-token";
const headers = { "x-simubench-token": TOKEN };

const BENCH = {
  schema: "simubench.bench/1",
  name: "edit-e2e",
  instruments: {
    awg: { model: "sb-awg-2", label: "函数发生器", state: { "ch1.amplitude": 2.0 } },
    scope: { model: "sb-dso-4", label: "示波器" },
  },
  duts: { rc: { type: "rc_lowpass", label: "RC 低通", params: { r: 1000, c: 1e-7 } } },
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
  await page.getByRole("radio", { name: "编辑" }).click();
  await expect(page.getByRole("tab", { name: "元件库" })).toBeVisible();
  return path;
}

test("add a DUT from the library, undo and redo", async ({ page }) => {
  const path = await openEditor(page);
  await page.getByRole("button", { name: "添加 电阻" }).click();
  await expect(page.locator(".react-flow__node")).toHaveCount(4);
  await expect(page.getByText("已保存到文件")).toBeVisible();
  expect(Object.keys((await readDoc(page.request, path)).data.duts as object)).toEqual(["rc", "r"]);
  // A topology change needs a restart; the banner says so.
  await expect(page.getByRole("button", { name: "重新启动以应用" })).toBeVisible();

  await page.keyboard.press("Control+z");
  await expect(page.locator(".react-flow__node")).toHaveCount(3);
  await expect(page.getByRole("button", { name: "重新启动以应用" })).toBeHidden();
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

  await page.getByRole("button", { name: "重新启动以应用" }).click();
  await expect(page.getByText("已按新的测试台定义重新启动")).toBeVisible();
  await page.getByRole("radio", { name: "运行" }).click();
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
    data: { path, base_sha256: doc.sha256, ops: [{ op: "add_dut", name: "buf", type: "unity_buffer", x: 360, y: 320 }] },
  });
  expect(r.ok()).toBeTruthy();
  await expect(page.getByText("已载入在其他地方所做的修改")).toBeVisible();
  await expect(page.locator(".react-flow__node")).toHaveCount(4);
});
