import { expect, type Page, test } from "@playwright/test";

// Screenshots of the workbench and the instrument screens at the laptop widths of #276 (#315):
// the oscilloscope, the multimeter and the source-measure unit, each bench on the command clock so
// nothing moves between runs.
const TOKEN = "e2e-token";
const headers = { "x-simubench-token": TOKEN };

const BENCHES = [
  { name: "scope", bench: "benches/keysight-dsox2024a.bench.yaml", nodes: 3, node: "示波器" },
  { name: "dmm", bench: "benches/keysight-34465a.bench.yaml", nodes: 4, node: "数字万用表" },
  { name: "smu", bench: "benches/keysight_b2901a.bench.yaml", nodes: 2, node: "精密源表" },
];

async function open(page: Page, bench: string, nodes: number) {
  const start = await page.request.post("/api/sessions", { headers, data: { bench_path: bench, force: true } });
  expect(start.ok(), await start.text()).toBeTruthy();
  const sid = ((await start.json()) as { id: string }).id;
  await page.goto(`/s/${sid}?token=${TOKEN}`);
  await expect(page.locator(".react-flow__node")).toHaveCount(nodes);
}

const shot = { maxDiffPixelRatio: 0.02, animations: "disabled" as const };
// The virtual clock, and the local addresses whose ports the service picks at random.
const masks = (page: Page) => [page.getByTitle("虚拟时间", { exact: true }), page.locator("code", { hasText: "127.0.0.1" })];

for (const [width, height] of [
  [1280, 800],
  [1440, 900],
]) {
  for (const b of BENCHES) {
    test(`${b.name} at ${width}×${height}: workbench and instrument screen`, async ({ page }) => {
      await page.setViewportSize({ width, height });
      await open(page, b.bench, b.nodes);
      const node = page.locator(".react-flow__node").filter({ hasText: b.node }).first();
      await node.click();
      await expect(page.getByText("身份与地址")).toBeVisible();
      await page.evaluate(() => document.fonts.ready);
      await expect(page).toHaveScreenshot(`workbench-${b.name}-${width}.png`, { ...shot, mask: masks(page) });

      await node.dblclick();
      await page.waitForTimeout(300); // the screen's first readings
      await page.evaluate(() => document.fonts.ready);
      await expect(page).toHaveScreenshot(`screen-${b.name}-${width}.png`, { ...shot, mask: masks(page) });
    });
  }
}
