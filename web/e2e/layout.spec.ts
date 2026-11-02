import { expect, type Page, test } from "@playwright/test";

// Layout at the widths people work at (#276, #277, #315): nothing in the inspector runs out of its
// column, the number fields show their values, the *IDN? string ends in an ellipsis with the full
// text on hover, the top bar stays on one line, and the canvas opens at a readable zoom.
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

const WIDTHS: [number, number][] = [
  [1280, 800],
  [1440, 900],
];

/** Elements of the inspector whose right edge passes the inspector's (0 when everything fits). */
async function inspectorOverflow(page: Page): Promise<number | string> {
  return page.evaluate(() => {
    const panel = [...document.querySelectorAll('[data-slot="scroll-area-viewport"]')].find((el) =>
      el.textContent?.includes("身份与地址"),
    ) as HTMLElement | undefined;
    if (!panel) return "no inspector";
    const right = panel.getBoundingClientRect().right;
    return [...panel.querySelectorAll("input, button, [role=switch], code")].filter((el) => el.getBoundingClientRect().right > right + 1).length;
  });
}

/** The *IDN? row fits its column: whole, or cut with an ellipsis and the full string as its title. */
async function expectIdnFits(page: Page) {
  const idn = page.locator("code").filter({ hasText: "," }).first();
  const fit = await idn.evaluate((el) => ({
    whole: el.scrollWidth <= el.clientWidth,
    ellipsis: getComputedStyle(el).textOverflow === "ellipsis",
    title: el.getAttribute("title") ?? "",
    text: el.textContent ?? "",
  }));
  expect(fit.whole || (fit.ellipsis && fit.title === fit.text)).toBeTruthy();
}

// The fields #276 reported blank at these widths, per instrument (#315).
const INSTRUMENTS = [
  { bench: "benches/keysight-34465a.bench.yaml", nodes: 4, node: "数字万用表", fields: ["量程设定", "积分时间（PLC）", "孔径时间"] },
  { bench: "benches/siglent_scope_rc.bench.yaml", nodes: 3, node: "函数发生器", fields: ["频率", "幅度", "偏置"] },
  { bench: "benches/keysight_b2901a.bench.yaml", nodes: 2, node: "精密源表", fields: ["电压设定", "电流设定"] },
];

for (const [width, height] of WIDTHS) {
  for (const inst of INSTRUMENTS) {
    test(`at ${width}×${height} the ${inst.node} inspector shows its fields and fits`, async ({ page }) => {
      await page.setViewportSize({ width, height });
      await open(page, inst.bench, inst.nodes);
      await page.locator(".react-flow__node").filter({ hasText: inst.node }).first().click();
      await expect(page.getByText("身份与地址")).toBeVisible();
      for (const name of inst.fields) {
        await expect(page.getByRole("textbox", { name, exact: true }).first()).toHaveValue(/\S/);
      }
      if (inst.node === "精密源表") {
        // The units stay beside the numbers and the source autorange switch is inside the column.
        await expect(page.getByRole("textbox", { name: "电压设定", exact: true })).toHaveValue(/V$/);
        await expect(page.getByRole("textbox", { name: "电流设定", exact: true })).toHaveValue(/A$/);
        await expect(page.getByRole("switch", { name: "电压源自动量程" })).toBeVisible();
      }
      expect(await inspectorOverflow(page)).toBe(0);
      await expectIdnFits(page);
    });
  }
}
