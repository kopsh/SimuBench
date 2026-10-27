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
  await expect(page.getByText(":CHANnel9:SCALe 1")).toBeVisible();
});

test("open the scope screen by double-clicking its node", async ({ page }) => {
  await startBench(page);
  await page.locator(".react-flow__node").filter({ hasText: "示波器" }).dblclick();
  await expect(page.getByRole("tab", { name: /示波器/ })).toBeVisible();
  await expect(page.getByRole("img", { name: "waveform" })).toBeVisible();
  await expect(page.getByText("实时预览")).toBeVisible();
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
