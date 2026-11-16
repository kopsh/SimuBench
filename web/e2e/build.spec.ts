import { expect, test } from "@playwright/test";

const TOKEN = "e2e-token";
const headers = { "x-simubench-token": TOKEN };

/** The definition file as stored, through the same API the acceptance page reads. */
async function stored(request: import("@playwright/test").APIRequestContext, id: string) {
  const res = await request.get(`/api/definitions/${id}/document`, { headers });
  return (await res.json()) as { data: Record<string, unknown>; ok: boolean };
}

test("build area: drag a component in, wire it, set a parameter, add a panel port, delete (#1201)", async ({ page, request }) => {
  // A workspace copy of a validation instrument: an optical receiver feeding an output stage.
  const copy = await request.post("/api/definitions/sb-oe-1/copy", { headers });
  const id = ((await copy.json()) as { data: { id: string } }).data.id;

  await page.goto(`/i/${id}?token=${TOKEN}`);
  await page.getByTestId("acceptance-tab-build").click();
  const area = page.getByTestId("build-area");
  await expect(area).toBeVisible();
  // The structure comes from the model it extends, and says so.
  await expect(area.getByText("继承自 sb-oe-1")).toBeVisible();
  await expect(page.getByTestId("build-component")).toHaveCount(2);

  // Drag an output stage from the library onto the canvas, below the per-channel band.
  await page.getByPlaceholder("按类型、名称或说明过滤").fill("output_stage");
  const pane = page.locator(".react-flow__pane");
  const box = (await pane.boundingBox())!;
  await page.getByTestId("build-library-output_stage").dragTo(pane, { targetPosition: { x: box.width / 2, y: box.height - 120 } });
  await expect(page.getByTestId("build-component")).toHaveCount(3);
  await expect(area.getByText("已保存")).toBeVisible();
  let doc = await stored(request, id);
  const names = (doc.data.components as { name: string }[]).map((c) => c.name);
  expect(names).toEqual(["orx", "out", "output_stage"]);

  // Wire the receiver's output to the new stage's input.
  await page
    .locator('[data-name="orx"] .react-flow__handle.source')
    .first()
    .dragTo(page.locator('[data-name="output_stage"] .react-flow__handle.target').first());
  await expect(page.locator(".react-flow__edge")).toHaveCount(2);
  await expect.poll(async () => JSON.stringify((await stored(request, id)).data.wiring)).toContain("orx.out");
  doc = await stored(request, id);
  expect((doc.data.wiring as { instrument: string[][] }).instrument).toContainEqual(["orx.out", "output_stage.in"]);

  // Its output impedance, typed into the side panel.
  await page.locator('[data-name="output_stage"]').click();
  await page.locator("#param-output_stage-z_out").fill("75");
  await page.locator("#param-output_stage-z_out").press("Enter");
  await expect
    .poll(async () => ((await stored(request, id)).data.components as { name: string; params?: { z_out?: number } }[])[2].params?.z_out)
    .toBe(75);

  // A panel port leading to it.
  await page.locator(".react-flow__pane").click({ position: { x: 8, y: 8 } });
  const ports = page.getByTestId("build-panel-ports");
  await ports.getByRole("textbox").fill("MON");
  await ports.getByRole("combobox").click();
  await page.getByRole("option", { name: "output_stage.pin" }).click();
  await ports.getByRole("button", { name: "添加" }).click();
  await expect(ports.getByText("MON")).toBeVisible();
  await expect.poll(async () => JSON.stringify((await stored(request, id)).data.ports)).toContain("MON");

  // Delete it with the keyboard: its wire and panel port go with it.
  await page.locator('[data-name="output_stage"]').click();
  await page.keyboard.press("Delete");
  await expect(page.getByTestId("build-component")).toHaveCount(2);
  await expect(page.getByText("连带 1 条连线、1 个面板端口")).toBeVisible();
  await expect.poll(async () => JSON.stringify((await stored(request, id)).data.ports ?? {})).not.toContain("MON");
  doc = await stored(request, id);
  expect(doc.ok).toBe(true);

  // Every save is a version in the change log, written by the Web.
  await page.getByTestId("acceptance-tab-history").click();
  await expect(page.getByText("output_stage").first()).toBeVisible();
});

test("build area of a built-in model is read only and points at the copy (#1201)", async ({ page }) => {
  await page.goto(`/i/siglent-sds2104xplus?token=${TOKEN}`);
  await page.getByTestId("acceptance-tab-build").click();
  await expect(page.getByText("内置型号不能直接修改")).toBeVisible();
  await expect(page.getByTestId("build-library")).toHaveCount(0);
  await expect(page.getByTestId("build-component").first()).toBeVisible();
  await expect(page.getByTestId("build-panel-ports").getByRole("button", { name: "添加" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "复制为我的仪表" })).toBeVisible();
});

test("build area: declare a setting, bind a parameter to it, give it a command, try it (#1206)", async ({ page, request }) => {
  const copy = await request.post("/api/definitions/sb-oe-1/copy", { headers });
  const id = ((await copy.json()) as { data: { id: string } }).data.id;
  await page.goto(`/i/${id}?token=${TOKEN}`);
  await page.getByTestId("acceptance-tab-build").click();
  await expect(page.getByTestId("build-area")).toBeVisible();

  // A setting: out.zout, a real number in ohms.
  await page.getByTestId("build-tab-settings").click();
  const settings = page.getByTestId("build-settings");
  await settings.getByPlaceholder("设置名，如 out.level").fill("out.zout");
  await settings.getByRole("button", { name: "添加设置" }).click();
  const row = page.getByTestId("build-setting-out.zout");
  await expect(row).toBeVisible();
  await row.getByLabel("out.zout 单位").fill("OHM");
  await row.getByLabel("out.zout 单位").press("Enter");
  await row.getByLabel("out.zout 默认值").fill("50");
  await row.getByLabel("out.zout 默认值").press("Enter");
  await expect
    .poll(async () => ((await stored(request, id)).data.state as Record<string, { unit?: string; default?: number }>)["out.zout"])
    .toMatchObject({ type: "float", unit: "OHM", default: 50 });

  // The output stage's impedance follows it.
  await page.locator('[data-name="out"]').click();
  await page.getByTestId("bind-out-z_out").click();
  await page.getByRole("option", { name: "out.zout" }).click();
  await expect(page.getByText("跟随设置 out.zout")).toBeVisible();
  await expect
    .poll(async () => ((await stored(request, id)).data.components as { name: string; params?: Record<string, unknown> }[]).find((c) => c.name === "out")?.params?.z_out)
    .toBe("$out.zout");

  // A command for it.
  await page.locator(".react-flow__pane").click({ position: { x: 8, y: 8 } });
  await page.getByTestId("build-tab-commands").click();
  const commands = page.getByTestId("build-commands");
  const before = await commands.getByTestId("build-command").count();
  await commands.getByPlaceholder("命令头，如 :OUTPut:LEVel").fill(":OUTPut:IMPedance");
  await commands.getByRole("combobox", { name: "绑定到" }).click();
  await page.getByRole("option", { name: "out.zout" }).click();
  await commands.getByRole("button", { name: "添加命令" }).click();
  await expect(commands.getByTestId("build-command")).toHaveCount(before + 1);
  await expect.poll(async () => JSON.stringify((await stored(request, id)).data.commands)).toContain(":OUTPut:IMPedance");
  expect((await stored(request, id)).ok).toBe(true);

  // The try run answers with what was set.
  await page.getByTestId("acceptance-tab-try").click();
  const command = page.getByRole("textbox", { name: "命令" });
  await command.fill(":OUTPut:IMPedance 75");
  await page.getByRole("button", { name: "发送" }).click();
  await command.fill(":OUTPut:IMPedance?");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.locator("pre").last()).toContainText("7.5");
});
