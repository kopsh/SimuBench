import { expect, type Page, test } from "@playwright/test";

import { expectValue, hasDigit, quantity } from "./judge";

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
    await expectValue(page.getByRole("textbox", { name: "时基" }).first(), hasDigit, "a number");

    // The top bar is one row: the clock is no taller than the bar.
    const clock = page.getByTitle("虚拟时间", { exact: true });
    expect((await clock.boundingBox())!.height).toBeLessThan(24);
  });
}

// The benches #277 named (titles near 8 px before) and the counter (#314): at the default view the
// node titles are at least 12 px and every node is inside the canvas.
for (const [bench, nodes] of [
  ["benches/awg_rc_scope.bench.yaml", 3],
  ["benches/keysight-dsox2024a.bench.yaml", 3],
  ["benches/keysight-34465a.bench.yaml", 4],
  ["benches/keysight_b2901a.bench.yaml", 2],
  ["benches/counter.bench.yaml", 2],
] as const) {
  test(`${bench}: the canvas opens with node titles at least 12 px and every node in view`, async ({ page }) => {
    await open(page, bench, nodes);
    const title = page.locator(".react-flow__node").first().locator(".font-medium").first();
    const size = await title.evaluate((el) => {
      const zoom = new DOMMatrixReadOnly(getComputedStyle(document.querySelector(".react-flow__viewport")!).transform).a;
      return parseFloat(getComputedStyle(el).fontSize) * zoom;
    });
    expect(size).toBeGreaterThanOrEqual(12);
    const outside = await page.evaluate(() => {
      const pane = document.querySelector(".react-flow")!.getBoundingClientRect();
      return [...document.querySelectorAll(".react-flow__node")]
        .map((n) => n.getBoundingClientRect())
        .filter((r) => r.left < pane.left - 1 || r.right > pane.right + 1 || r.top < pane.top - 1 || r.bottom > pane.bottom + 1).length;
    });
    expect(outside).toBe(0);
  });
}

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
  // The identity section starts folded to the virtual address (#1355): open it to see *IDN?.
  const identity = page.getByTestId("identity-section");
  if ((await identity.getAttribute("data-open")) === null) await identity.getByRole("button").first().click();
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
        await expectValue(page.getByRole("textbox", { name, exact: true }).first(), (v) => v.trim() !== "", `${name} is not blank`);
      }
      if (inst.node === "精密源表") {
        // The units stay beside the numbers and the source autorange switch is inside the column.
        await expectValue(page.getByRole("textbox", { name: "电压设定", exact: true }), (v) => quantity(v, "V") !== null, "a voltage with its unit");
        await expectValue(page.getByRole("textbox", { name: "电流设定", exact: true }), (v) => quantity(v, "A") !== null, "a current with its unit");
        await expect(page.getByRole("switch", { name: "电压源自动量程" })).toBeVisible();
      }
      expect(await inspectorOverflow(page)).toBe(0);
      await expectIdnFits(page);
    });
  }
}

// A narrow window and a narrow inspector (#1354): the canvas fits every node at a readable zoom, and
// the inspector's actions keep their icons side by side instead of running into each other.
async function actionsApart(page: Page) {
  const boxes = await page
    .getByTestId("instrument-actions")
    .getByRole("button")
    .evaluateAll((els) => els.map((e) => e.getBoundingClientRect()).map((r) => ({ left: r.left, right: r.right, top: r.top })));
  const panel = (await page.getByTestId("inspector-panel").boundingBox())!;
  for (const b of boxes) expect(b.right, "an action past the inspector's edge").toBeLessThanOrEqual(panel.x + panel.width + 1);
  const row = boxes.filter((b) => Math.abs(b.top - boxes[boxes.length - 1].top) < 2).sort((a, b) => a.left - b.left);
  for (let i = 1; i < row.length; i++) expect(row[i].left, "two actions overlap").toBeGreaterThanOrEqual(row[i - 1].right - 1);
}

test("at 1024×700 every node is in view and the inspector's actions do not overlap (#1354)", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 700 });
  await open(page, "benches/awg_rc_scope.bench.yaml", 3);
  await page.locator(".react-flow__node").filter({ hasText: "函数发生器" }).click();
  await expect(page.getByTestId("instrument-actions")).toBeVisible();
  await expect
    .poll(() =>
      page.evaluate(() => {
        const pane = document.querySelector(".react-flow")!.getBoundingClientRect();
        return [...document.querySelectorAll(".react-flow__node")]
          .map((n) => n.getBoundingClientRect())
          .filter((r) => r.left < pane.left - 1 || r.right > pane.right + 1 || r.top < pane.top - 1 || r.bottom > pane.bottom + 1).length;
      }),
    )
    .toBe(0);
  await actionsApart(page);
});

test("with the assistant open the inspector's actions do not overlap (#1354)", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await open(page, "benches/awg_rc_scope.bench.yaml", 3);
  await page.locator(".react-flow__node").filter({ hasText: "函数发生器" }).click();
  await page.getByRole("button", { name: "AI 助手" }).first().click();
  await expect(page.getByText("AI 助手").last()).toBeVisible();
  await actionsApart(page);
});

// #1459: a level just under 1 V rounds before its prefix is chosen — the function generator's wire
// on the multimeter bench reads "1 V DC", never "1.00e+3 mV DC".
test("benches/keysight-34465a.bench.yaml: the canvas wire levels never show an exponent", async ({ page }) => {
  await open(page, "benches/keysight-34465a.bench.yaml", 4);
  const readouts = page.getByTestId("wire-readout");
  await expect(readouts.first()).toBeVisible();
  await expect(readouts.filter({ hasText: "e+" })).toHaveCount(0);
  await expect(readouts.locator("span.text-foreground").getByText("1 V", { exact: true })).toBeVisible();
});
