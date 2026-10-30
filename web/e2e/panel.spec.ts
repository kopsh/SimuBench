import { expect, type Page, test } from "@playwright/test";

// Front panels drawn from the definition (issue 128, instrument-authoring.md 7.3): the class
// defaults of a multimeter and a calibrator, a declared panel, and build mode's initial settings.
const TOKEN = "e2e-token";
const headers = { "x-simubench-token": TOKEN };

const panel = (page: Page) => page.getByTestId("instrument-panel");

async function open(page: Page, bench: string, node: string, nodes: number) {
  const start = await page.request.post("/api/sessions", { headers, data: { bench_path: bench } });
  expect(start.ok(), await start.text()).toBeTruthy();
  const sid = ((await start.json()) as { id: string }).id;
  await page.goto(`/s/${sid}?token=${TOKEN}`);
  await expect(page.locator(".react-flow__node")).toHaveCount(nodes);
  await page.locator(".react-flow__node").filter({ hasText: node }).dblclick();
  await expect(page.getByTestId("instrument-panel")).toBeVisible();
  return sid;
}

test("34465A default panel: function and range keys change the reading", async ({ page }) => {
  await open(page, "benches/keysight-34465a.bench.yaml", "数字万用表", 4);
  await expect(page.getByText("类别默认布局")).toBeVisible();
  const display = panel(page).getByTestId("panel-readout-meter.DC");
  // 1 V into 900 kΩ / (100 kΩ ∥ 10 MΩ): 99.103 mV on the 100 mV range the bench sets.
  await expect(display).toHaveText(/^99\.103/);
  const range = panel(page).getByRole("group", { name: "量程设定" });
  await expect(range.getByRole("button", { name: "100 mV" })).toHaveAttribute("aria-pressed", "true");

  // The 1000 V range resolves 1 mV less finely: the same input reads 99.0 mV.
  await range.getByRole("button", { name: "1000 V" }).click();
  await expect(range.getByRole("button", { name: "1000 V" })).toHaveAttribute("aria-pressed", "true");
  await expect(display).toHaveText(/^99\.0000/);
  await range.getByRole("button", { name: "AUTO" }).click();
  await expect(range.getByRole("button", { name: "AUTO" })).toHaveAttribute("aria-pressed", "true");
  await expect(panel(page).getByRole("switch", { name: "自动量程" })).toHaveCount(0); // the AUTO key is the autorange

  // AC: the main display shows the AC reading, 0 V for a DC input.
  const fn = panel(page).getByRole("group", { name: "测量功能" });
  await fn.getByRole("button", { name: "AC V" }).click();
  await expect(fn.getByRole("button", { name: "AC V" })).toHaveAttribute("aria-pressed", "true");
  await expect(panel(page).getByTestId("panel-readout-meter.DC")).toHaveCount(0);
  await expect(panel(page).getByTestId("panel-readout-meter.AC")).toHaveText(/^0\.0/);

  // NPLC and input impedance are on the panel too.
  await expect(panel(page).getByRole("textbox", { name: "积分时间（PLC）" })).toBeVisible();
  await expect(panel(page).getByRole("switch", { name: "输入阻抗自动（10 MΩ ↔ >10 GΩ）" })).toBeVisible();
});

test("5522A default panel: set the output, operate and standby", async ({ page }) => {
  await open(page, "benches/fluke-5522a-dmm.bench.yaml", "5522A", 2);
  const terminal = panel(page).getByTestId("panel-readout-out.output");
  await expect(terminal).toHaveText("STBY");

  const level = panel(page).getByRole("textbox", { name: "幅度设定" });
  await level.fill("2.5");
  await level.press("Enter");
  await expect(panel(page).getByTestId("panel-readout-out.level")).toHaveText("2.500000");

  const keys = panel(page).getByRole("group", { name: "工作" });
  await keys.getByRole("button", { name: "OPER" }).click();
  await expect(terminal).toHaveText("OPER");
  await expect(keys.getByRole("button", { name: "OPER" })).toHaveAttribute("aria-pressed", "true");
  await expect(keys.getByRole("button", { name: "STBY" })).toHaveAttribute("aria-pressed", "false");
  // After the 5 s settling time the meter on the output reads it (within the hidden deviation).
  await page.getByRole("combobox", { name: "推进" }).click();
  await page.getByRole("option", { name: "1 s" }).click();
  for (let i = 0; i < 6; i++) await page.getByRole("button", { name: /推进/ }).click();
  await page.getByRole("tab", { name: "画布" }).click();
  await page.locator(".react-flow__node").filter({ hasText: "SB-DMM-1" }).dblclick();
  await expect(panel(page).getByTestId("panel-readout-meter.DC")).toHaveText(/^2\.4999|^2\.5000/);

  await page.getByRole("tab", { name: "cal" }).click();
  await keys.getByRole("button", { name: "STBY" }).click();
  await expect(terminal).toHaveText("STBY");
  await expect(keys.getByRole("button", { name: "STBY" })).toHaveAttribute("aria-pressed", "true");
  // ACV puts the frequency on the display.
  await expect(panel(page).getByTestId("panel-readout-out.frequency")).toHaveCount(0);
  await panel(page).getByRole("group", { name: "功能" }).getByRole("button", { name: "ACV" }).click();
  await expect(panel(page).getByTestId("panel-readout-out.frequency")).toBeVisible();
});

test("a declared panel: its groups, controls and softkeys", async ({ page }) => {
  await open(page, "benches/panel.bench.yaml", "面板万用表", 2);
  await expect(page.getByText("定义的面板")).toBeVisible();
  for (const name of ["功能", "量程", "高级"]) await expect(panel(page).getByRole("region", { name })).toBeVisible();
  await expect(panel(page).getByTestId("panel-readout-meter.DC")).toHaveText(/^4\.99|^5\.00/);

  // Buttons, a number with step keys, a drop-down and a switch, as declared.
  await panel(page).getByRole("group", { name: "量程" }).getByRole("button", { name: "1 V" }).click();
  await expect(panel(page).getByTestId("panel-readout-meter.DC")).toHaveText("OVLD");
  const nplc = panel(page).getByRole("textbox", { name: "NPLC" });
  await expect(nplc).toHaveValue("1");
  await panel(page).getByRole("button", { name: "NPLC 增大一步" }).click();
  await expect(nplc).toHaveValue("1.5");
  await expect(panel(page).getByRole("combobox", { name: "输入阻抗" })).toBeVisible();
  await expect(panel(page).getByRole("switch", { name: "蜂鸣器" })).toBeChecked();

  const soft = panel(page).getByRole("group", { name: "软键" });
  await soft.getByRole("button", { name: "精确" }).click();
  await expect(soft.getByRole("button", { name: "精确" })).toHaveAttribute("aria-pressed", "true");
  await expect(nplc).toHaveValue("10");
});

test("in build mode the panel sets the bench file's initial settings", async ({ page }) => {
  const path = `e2e/panel-${Date.now()}.bench.yaml`;
  const source = await page.request.get(`/api/benches/file?path=${encodeURIComponent("benches/panel.bench.yaml")}`, { headers });
  const data = ((await source.json()) as { data: { instruments: { awg: { state: Record<string, unknown> } } } }).data;
  data.instruments.awg.state["ch1.load"] = "INF"; // JSON has no infinity: the file's .inf reads back as null
  const put = await page.request.put("/api/benches/file", { headers, data: { path, base_sha256: null, data } });
  expect(put.ok()).toBeTruthy();
  const sid = await open(page, path, "面板万用表", 2);

  await page.getByRole("radio", { name: "搭建" }).click();
  await page.getByRole("tab", { name: /面板万用表/ }).click(); // build mode opens on the canvas
  await expect(page.getByText("面板设置的是初始设置")).toBeVisible();
  const range = panel(page).getByRole("group", { name: "量程" });
  await range.getByRole("button", { name: "10 V" }).click();
  await expect(range.getByRole("button", { name: "10 V" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText("已保存到文件")).toBeVisible();

  // The file has the key's setting and the autorange it turns off; the running meter is unchanged.
  const doc = await page.request.get(`/api/benches/file?path=${encodeURIComponent(path)}`, { headers });
  const instruments = ((await doc.json()) as { data: { instruments: Record<string, { state?: Record<string, unknown> }> } }).data
    .instruments;
  expect(instruments.dmm.state).toEqual({ "sense.range": 10, "sense.range_auto": false });
  const info = await page.request.get(`/api/sessions/${sid}`, { headers });
  const dmm = ((await info.json()) as { instruments: { name: string; state: { path: string; value: unknown }[] }[] }).instruments.find(
    (i) => i.name === "dmm",
  );
  expect(dmm?.state.find((v) => v.path === "sense.range_auto")?.value).toBe(true);
  await expect(page.getByText(/量程：0 V（初始：10 V）|自动量程：开（初始：关）/).first()).toBeVisible();
});
