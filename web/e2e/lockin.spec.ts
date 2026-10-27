import { expect, test } from "@playwright/test";

const TOKEN = "e2e-token";

test("lock-in screen: readouts follow the circuit and settle after a change", async ({ page }) => {
  await page.goto(`/?token=${TOKEN}`);
  await page.getByRole("listitem").filter({ hasText: "lockin_rc" }).getByRole("button", { name: "启动" }).click();
  await expect(page.locator(".react-flow__node")).toHaveCount(3);
  await page.locator(".react-flow__node").filter({ hasText: "锁相放大器" }).dblclick();
  await expect(page.getByText("已锁定")).toBeVisible();
  // 0.5 Vrms at the physics-correct corner 1515.76 Hz: R = 353.51 mV, θ = −44.99°
  await expect(page.getByTestId("readout-R")).toHaveText(/^353\.5/);
  await expect(page.getByTestId("readout-θ")).toHaveText(/^-44\.99/);

  const freq = page.getByRole("textbox", { name: "参考频率" }).first();
  await freq.fill("100");
  await freq.press("Enter");
  // Right after the change the output is still near the old value; after advancing it settles.
  await expect(page.getByTestId("readout-R")).toHaveText(/^353\.5/);
  for (let i = 0; i < 4; i++) {
    await page.getByRole("button", { name: /推进/ }).click();
  }
  await page.getByRole("combobox", { name: "推进" }).click();
  await page.getByRole("option", { name: "1 s" }).click();
  await page.getByRole("button", { name: /推进/ }).click();
  await expect(page.getByTestId("readout-R")).toHaveText(/^498\.8/);
});
