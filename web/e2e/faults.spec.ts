import { expect, test } from "@playwright/test";

const TOKEN = "e2e-token";

test("inject a timeout on the generator link, see it everywhere, clear it", async ({ page }) => {
  await page.goto(`/?token=${TOKEN}`);
  await page.getByRole("listitem").filter({ hasText: "awg_rc_scope" }).getByRole("button", { name: /^(打开测试台|进入)$/ }).click();
  await expect(page.locator(".react-flow__node")).toHaveCount(3);
  const awg = page.locator(".react-flow__node").filter({ hasText: "函数发生器" });
  await awg.click();

  await page.getByRole("button", { name: "故障注入" }).click();
  await expect(page.getByRole("combobox", { name: "故障类型" })).toContainText("超时");
  await page.getByRole("button", { name: "注入", exact: true }).click();
  await expect(page.getByText(/已注入故障/)).toBeVisible();
  await expect(awg.getByLabel("故障注入")).toBeVisible();

  const console = page.getByRole("textbox", { name: "发送 SCPI 命令" });
  await console.fill("*IDN?");
  await console.press("Enter");
  await expect(page.getByRole("status", { name: "响应" })).toContainText("没有回应（超时）");

  await page.getByRole("tab", { name: /问题/ }).click();
  await expect(page.getByText("生效中").first()).toBeVisible();

  await page.getByRole("button", { name: "清除故障" }).first().click();
  await expect(awg.getByLabel("故障注入")).toBeHidden();
  await console.fill("*IDN?");
  await console.press("Enter");
  await expect(page.getByRole("status", { name: "响应" })).toContainText("SIMUBENCH,SB-AWG-2");
});
