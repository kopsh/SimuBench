import { expect, type Page, test } from "@playwright/test";

const TOKEN = "e2e-token";

async function startBench(page: Page) {
  await page.goto(`/?token=${TOKEN}`);
  await expect(page.getByRole("heading", { name: "测试台", exact: true })).toBeVisible();
  await page.getByRole("listitem").filter({ hasText: "awg_rc_scope" }).getByRole("button", { name: "启动" }).click();
  await expect(page).toHaveURL(/\/s\/[0-9a-f]+$/);
  await expect(page.locator(".react-flow__node")).toHaveCount(3);
}

test("the token is removed from the address bar", async ({ page }) => {
  await page.goto(`/?token=${TOKEN}`);
  await expect(page.getByRole("heading", { name: "测试台", exact: true })).toBeVisible();
  expect(page.url()).not.toContain("token");
});

test("a wrong token explains what to do", async ({ browser }) => {
  const page = await (await browser.newContext()).newPage();
  await page.goto("/?token=wrong");
  await expect(page.getByText("访问令牌无效")).toBeVisible();
});

test("start a bench, inspect and edit an instrument", async ({ page }) => {
  await startBench(page);
  await page.locator(".react-flow__node").filter({ hasText: "函数发生器" }).click();
  const inspector = page.getByText("身份与地址").locator("xpath=ancestor::div[contains(@class,'space-y-4')]");
  await expect(inspector.getByText("TCPIP0::192.168.1.20::5025::SOCKET")).toBeVisible();

  const freq = page.getByRole("textbox", { name: "频率" }).first();
  await freq.fill("2.5k");
  await freq.press("Enter");
  await expect(page.locator(".react-flow__node").filter({ hasText: "函数发生器" })).toContainText("2.5 kHz");

  // Out of range: the change is refused and the field explains why.
  await freq.fill("1G");
  await freq.press("Enter");
  await expect(page.getByLabel(/-222/)).toBeVisible();
});

test("SCPI console, communication log and problems", async ({ page }) => {
  await startBench(page);
  await page.getByRole("button", { name: "示波器" }).first().click();
  const input = page.getByRole("textbox", { name: "发送 SCPI 命令" });
  await input.fill("*IDN?");
  await input.press("Enter");
  await expect(page.getByText(/SIMUBENCH,SB-DSO-4/).first()).toBeVisible();
  await input.fill(":CHANnel9:SCALe 1");
  await input.press("Enter");
  await expect(page.getByRole("tab", { name: /问题/ })).toContainText("1");
  await page.getByRole("tab", { name: /问题/ }).click();
  await expect(page.getByRole("button", { name: /-114.*Header suffix out of range/ })).toBeVisible();
  await page.getByRole("tab", { name: "通信" }).click();
  const row = page.getByText(":CHANnel9:SCALe 1").locator("..");
  await expect(row).toBeVisible();
  await expect(row).toContainText("界面"); // who sent it
  await expect(row.getByTestId("wall-time")).toHaveText(/^\d\d:\d\d:\d\d\.\d{3}$/); // real time beside virtual time
});

test("open the scope screen by double-clicking its node", async ({ page }) => {
  await startBench(page);
  await page.locator(".react-flow__node").filter({ hasText: "示波器" }).dblclick();
  await expect(page.getByRole("tab", { name: /示波器/ })).toBeVisible();
  await expect(page.getByRole("img", { name: "waveform" })).toBeVisible();
  await expect(page.getByText("实时预览")).toBeVisible();
});

test("acquire from the scope screen without a script", async ({ page }) => {
  await startBench(page);
  const controls = page.getByRole("group", { name: "采集控制" });
  await page.locator(".react-flow__node").filter({ hasText: "示波器" }).dblclick();
  // Outputs are off at power-on: the notice traces CH1 back to the generator, through the probe and the RC.
  const why = page.getByRole("status", { name: "输入为 0 V 的原因" });
  await expect(why).toContainText("探头 10×");
  await expect(why).toContainText("RC 低通");
  await expect(why).toContainText("函数发生器 OUT1 输出关闭");
  await page.getByRole("radio", { name: "最近采集" }).click();
  await expect(page.getByText("还没有采集")).toBeVisible();
  await controls.getByRole("button", { name: "单次" }).click();
  await expect(page.getByText("还没有采集")).toBeHidden();
  await expect(page.getByText(/采集次数\s*1/)).toBeVisible();
  await expect(page.getByText(":SINGle").first()).toBeVisible(); // in the communication log, like a script's
  await controls.getByRole("button", { name: "运行" }).click();
  await expect(page.getByText(/采集次数\s*[3-9]/)).toBeVisible();
  await controls.getByRole("button", { name: "停止" }).click();
  await expect(controls.getByRole("button", { name: "单次" })).toBeEnabled();
});

test("an unwired channel says so; clicking the source selects it", async ({ page }) => {
  await startBench(page);
  await page.locator(".react-flow__node").filter({ hasText: "示波器" }).dblclick();
  const input = page.getByRole("textbox", { name: "发送 SCPI 命令" });
  await input.fill(":CHANnel2:DISPlay ON");
  await input.press("Enter");
  const why = page.getByRole("status", { name: "输入为 0 V 的原因" });
  await expect(why).toContainText("没有接线");
  await page.getByRole("button", { name: "函数发生器 OUT1 输出关闭" }).click();
  await expect(page.getByText("TCPIP0::192.168.1.20::5025::SOCKET")).toBeVisible(); // the generator is selected
});

test("open screens are remembered for the bench and reopen on the next start", async ({ page }) => {
  await startBench(page);
  await page.locator(".react-flow__node").filter({ hasText: "示波器" }).dblclick();
  await expect(page.getByRole("tab", { name: /示波器/ })).toBeVisible();
  await page.getByRole("button", { name: "返回测试台列表" }).click();
  // Start again: the scope screen comes back as the active tab, not the canvas.
  await page.getByRole("listitem").filter({ hasText: "awg_rc_scope" }).getByRole("button", { name: "启动" }).click();
  await expect(page).toHaveURL(/\/s\/[0-9a-f]+$/);
  await expect(page.getByRole("tab", { name: /示波器/ })).toBeVisible();
  await expect(page.getByRole("group", { name: "采集控制" })).toBeVisible();
});

test("switch to English and dark theme", async ({ page }) => {
  await startBench(page);
  await page.getByRole("button", { name: "设置" }).click();
  await page.getByRole("menuitemradio", { name: "English" }).click();
  await expect(page.getByText("Resources").or(page.getByText("Instruments")).first()).toBeVisible();
  await page.getByRole("button", { name: "Settings" }).click();
  await page.getByRole("menuitemradio", { name: "Dark" }).click();
  await expect(page.locator("html")).toHaveClass(/dark/);
});

test("run record: open it from the home page, see the timeline, replay identically", async ({ page }) => {
  await page.goto(`/?token=${TOKEN}`);
  await page.getByRole("listitem").filter({ hasText: "awg_rc_scope" }).getByRole("button", { name: "启动" }).click();
  await expect(page.locator(".react-flow__node")).toHaveCount(3);
  const input = page.getByRole("textbox", { name: "发送 SCPI 命令" });
  await page.locator(".react-flow__node").filter({ hasText: "示波器" }).click();
  for (const cmd of [":DIGitize", ":WAVeform:DATA?", ":WAVeform:PREamble?"]) {
    await input.fill(cmd);
    await input.press("Enter");
    await expect(page.getByRole("status", { name: "响应" })).not.toBeEmpty();
  }

  // This session's record (other tests' sessions on the same bench write records too).
  const sid = new URL(page.url()).pathname.split("/").pop()!;
  const info = (await (await page.request.get(`/api/sessions/${sid}`, { headers: { "x-simubench-token": TOKEN } })).json()) as {
    record_path: string;
  };
  const runId = info.record_path.split(/[\\/]/).pop()!.replace(/^run-/, "").replace(/\.jsonl$/, "");
  await page.getByRole("button", { name: "返回测试台列表" }).click();
  const runs = page.getByRole("region", { name: "运行记录" });
  await runs.locator(`a[href="/runs/${runId}"]`).click();
  await expect(page.getByTestId("run-timeline")).toBeVisible();
  const events = page.getByTestId("run-events");
  await expect(events).toContainText(":WAVeform:PREamble?");
  await expect(events).toContainText("由采集 #"); // the data block is kept as its recipe
  await page.getByRole("button", { name: "回放并比较" }).click();
  await expect(page.getByRole("status", { name: "回放结果" })).toContainText("回放一致");
});

test("virtual probe: select a net and see why it reads what it reads", async ({ page }) => {
  await page.goto(`/?token=${TOKEN}`);
  await page.getByRole("listitem").filter({ hasText: "awg_rc_scope" }).getByRole("button", { name: "启动" }).click();
  await expect(page.locator(".react-flow__node")).toHaveCount(3);
  await page.getByRole("button", { name: /^net-1/ }).click();
  const probe = page.getByTestId("probe");
  await expect(probe).toContainText("没有信号源连到这个网络"); // the generator's output is off
  const input = page.getByRole("textbox", { name: "发送 SCPI 命令" });
  await page.locator(".react-flow__node").filter({ hasText: "函数发生器" }).click();
  await input.fill(":OUTPut1:STATe ON");
  await input.press("Enter");
  await page.getByRole("button", { name: /^net-1/ }).click();
  await expect(page.getByTestId("probe-sources")).toContainText("awg · ch1.out");
  await expect(page.getByTestId("probe-sources")).toContainText("∠");
});

test("about and diagnostics: manifest, health checks, bundle download", async ({ page }) => {
  await page.goto(`/?token=${TOKEN}`);
  await page.getByRole("button", { name: "设置" }).click();
  await page.getByRole("menuitem", { name: "关于与诊断" }).click();
  await expect(page.getByTestId("about-version")).toHaveText(/^\d+\.\d+\.\d+/);
  await expect(page.getByTestId("about-doctor")).toContainText("pyvisa backend");
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出诊断包" }).click();
  expect((await download).suggestedFilename()).toMatch(/^simubench-diagnostics-.*\.zip$/);
});

test("first-use guide: three ways in, dismissed for good", async ({ page }) => {
  await page.goto(`/?token=${TOKEN}`);
  const guide = page.getByRole("region", { name: "开始使用 SimuBench" });
  await expect(guide).toBeVisible();
  await expect(guide).toContainText("@simubench");
  await expect(guide).toContainText("simubench mcp");
  await guide.getByRole("button", { name: "不再显示" }).click();
  await expect(guide).toBeHidden();
  await page.reload();
  await expect(page.getByRole("heading", { name: "测试台", exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "开始使用 SimuBench" })).toBeHidden();
});
