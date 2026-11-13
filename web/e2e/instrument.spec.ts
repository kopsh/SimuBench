import { execFileSync } from "node:child_process";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test } from "@playwright/test";

import { openButton } from "./judge";

const TOKEN = "e2e-token";
const ROOT = resolve(import.meta.dirname, "..", "..");
// The workspace web/e2e/serve.mjs builds, so a test can edit a definition the way an agent does.
const WORKSPACE = join(tmpdir(), "simubench-e2e-workspace");

test("acceptance page: functions, commands, trial run and cases of a real model", async ({ page }) => {
  await page.goto(`/?token=${TOKEN}`);
  // The home screen's instrument list opens the acceptance page without a bench.
  await page.getByTestId("instrument-list").getByRole("link", { name: "34465A Keysight Technologies" }).click();
  await expect(page.getByRole("heading", { name: "34465A" })).toBeVisible();
  await expect(page.getByText("真实型号")).toBeVisible();
  await expect(page.getByText("34460-90901")).toBeVisible();
  // Overview: the functions and ranges come from the definition's measurement chain.
  await expect(page.getByText("量程", { exact: true })).toBeVisible();
  await expect(page.getByText("1000 V", { exact: true })).toBeVisible();
  await expect(page.getByText("AUTO", { exact: true })).toBeVisible();

  // Commands: the coverage table's level next to the definition's parameters.
  await page.getByTestId("acceptance-tab-commands").click();
  await page.getByPlaceholder("搜索命令、参数或说明").fill("NPLCycles");
  await expect(page.getByText("[:SENSe]:VOLTage[:DC]:NPLCycles")).toBeVisible();
  await expect(page.getByText("实现", { exact: true }).first()).toBeVisible();

  // Try run: the temporary session builds the definition and answers.
  await page.getByTestId("acceptance-tab-try").click();
  await page.getByRole("textbox", { name: "命令" }).fill("*IDN?");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText("Keysight Technologies,34465A,MY57440001").last()).toBeVisible();

  // Cases: a failing case shows expected and actual; the all-pass file passes.
  await page.getByTestId("acceptance-tab-cases").click();
  await page.getByRole("button", { name: "cases/keysight-34465a-fail.cases.yaml" }).click();
  await page.getByRole("button", { name: "运行用例" }).click();
  await expect(page.getByText("失败 1")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("ACME,DM1")).toBeVisible();
  await page.getByRole("button", { name: "cases/keysight-34465a.cases.yaml" }).click();
  await page.getByRole("button", { name: "运行用例" }).click();
  await expect(page.getByText("通过 2")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("失败 1")).toBeHidden();
});

test("acceptance overview lists the model's options and what each changes (#1007)", async ({ page }) => {
  await page.goto(`/?token=${TOKEN}`);
  await page.getByTestId("instrument-list").getByRole("link", { name: "N9320B Keysight Technologies" }).click();
  const options = page.getByTestId("overview-options");
  await expect(options).toContainText("P03");
  await expect(options).toContainText("input.preamp_gain, input.preamp_noise_figure");
  await expect(options).toContainText("[:SENSe]:POWer[:RF]:GAIN[:STATe]");
});

test("acceptance page reloads a changed definition and shows the change in the instrument's language", async ({ page }) => {
  await page.goto(`/?token=${TOKEN}`);
  await page.getByTestId("instrument-list").getByRole("link", { name: "34465A Keysight Technologies" }).click();
  await expect(page.getByRole("heading", { name: "34465A" })).toBeVisible();
  // An agent edits the workspace copy (the save path behind MCP's edit_definition).
  execFileSync("uv", ["run", "python", "web/e2e/edit_definition.py", WORKSPACE, "keysight-34465a"], {
    cwd: ROOT,
    stdio: "pipe",
    timeout: 60_000,
  });
  await expect(page.getByText("已载入新的修改")).toBeVisible({ timeout: 20_000 });
  await page.getByTestId("acceptance-tab-history").click();
  // v1 is the file as it was before SimuBench wrote it (recorded as external), v2 the agent's save.
  await expect(page.getByText("v2", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Agent", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("sense.range").first()).toBeVisible();
  await expect(page.getByText("最大值").first()).toBeVisible();
  await expect(page.getByText("2000", { exact: true }).first()).toBeVisible();
});

test("the acceptance page opens as a workbench tab beside the internals view", async ({ page }) => {
  await page.goto(`/?token=${TOKEN}`);
  await openButton(page.getByRole("listitem").filter({ hasText: "keysight_34465a_divider" }))
    .click();
  await page.locator(".react-flow__node").filter({ hasText: "34465A" }).click();
  await page.getByRole("button", { name: "验收页" }).click();
  await expect(page.getByRole("tab", { name: "数字万用表 · 验收" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "概览" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "34465A" })).toBeVisible();
  // The page is read only: the definition is inspected and tried, never edited here.
  await expect(page.getByRole("tab", { name: "命令" })).toBeVisible();
  await expect(page.getByRole("button", { name: "编辑定义" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "保存定义" })).toHaveCount(0);
});
