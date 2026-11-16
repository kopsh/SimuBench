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
