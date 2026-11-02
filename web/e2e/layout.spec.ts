import { expect, type Page, test } from "@playwright/test";

// Layout at the widths people work at (#276, #277): nothing in the inspector runs out of its
// column, the top bar stays on one line, and the canvas opens at a readable zoom.
const TOKEN = "e2e-token";
const headers = { "x-simubench-token": TOKEN };

async function open(page: Page, bench: string, nodes: number) {
  const start = await page.request.post("/api/sessions", { headers, data: { bench_path: bench } });
  expect(start.ok(), await start.text()).toBeTruthy();
  const sid = ((await start.json()) as { id: string }).id;
  await page.goto(`/s/${sid}?token=${TOKEN}`);
  await expect(page.locator(".react-flow__node")).toHaveCount(nodes);
}

for (const [width, height] of [
  [1280, 800],
  [1440, 900],
]) {
  test(`at ${width}×${height} the inspector and the top bar fit`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await open(page, "benches/awg_rc_scope.bench.yaml", 3);
    await page.locator(".react-flow__node").filter({ hasText: "示波器" }).click();
    await expect(page.getByText("身份与地址")).toBeVisible();

    // No element of the inspector reaches past the viewport's right edge.
    const overflow = await page.evaluate(() => {
      const panel = [...document.querySelectorAll('[data-slot="scroll-area-viewport"]')].find((el) =>
        el.textContent?.includes("身份与地址"),
      ) as HTMLElement | undefined;
      if (!panel) return "no inspector";
      const right = panel.getBoundingClientRect().right;
      const out = [...panel.querySelectorAll("input, button")].filter((el) => el.getBoundingClientRect().right > right + 1);
      return out.length;
    });
    expect(overflow).toBe(0);
    // A number field shows its value with the unit (the timebase is never blank).
    await expect(page.getByRole("textbox", { name: "时基" }).first()).toHaveValue(/\d/);

    // The top bar is one row: the clock is no taller than the bar.
    const clock = page.getByTitle("虚拟时间", { exact: true });
    expect((await clock.boundingBox())!.height).toBeLessThan(24);
  });
}

test("the canvas opens at a zoom where node titles are at least 12 px", async ({ page }) => {
  await open(page, "benches/awg_rc_scope.bench.yaml", 3);
  const title = page.locator(".react-flow__node").first().locator(".font-medium").first();
  const size = await title.evaluate((el) => {
    const zoom = new DOMMatrixReadOnly(getComputedStyle(document.querySelector(".react-flow__viewport")!).transform).a;
    return parseFloat(getComputedStyle(el).fontSize) * zoom;
  });
  expect(size).toBeGreaterThanOrEqual(12);
});
