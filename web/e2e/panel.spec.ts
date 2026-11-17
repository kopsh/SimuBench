import { expect, type Locator, type Page, test } from "@playwright/test";

import { expectNear } from "./judge";

// Front panels drawn from the definition (issue 128, instrument-authoring.md 7.3): the class
// defaults of a multimeter and a calibrator, a declared panel, and build mode's initial settings.
const TOKEN = "e2e-token";
const headers = { "x-simubench-token": TOKEN };

const panel = (page: Page) => page.getByTestId("instrument-panel");

async function open(page: Page, bench: string, node: string, nodes: number) {
  const start = await page.request.post("/api/sessions", {
    headers,
    data: { bench_path: bench },
  });
  expect(start.ok(), await start.text()).toBeTruthy();
  const sid = ((await start.json()) as { id: string }).id;
  await page.goto(`/s/${sid}?token=${TOKEN}`);
  await expect(page.locator(".react-flow__node")).toHaveCount(nodes);
  await page.locator(".react-flow__node").filter({ hasText: node }).dblclick();
  await expect(page.getByTestId("instrument-panel")).toBeVisible();
  return sid;
}

const PREFIX: Record<string, number> = {
  p: 1e-12,
  n: 1e-9,
  µ: 1e-6,
  u: 1e-6,
  m: 1e-3,
  "": 1,
  k: 1e3,
  M: 1e6,
};

/** A panel readout as a number in base units: its mantissa times the prefix of the unit beside it. */
async function readoutValue(readout: Locator): Promise<number> {
  const mantissa = Number.parseFloat((await readout.textContent()) ?? "");
  const unit =
    (await readout.locator("xpath=following-sibling::span[1]").textContent()) ??
    "";
  const prefix = unit.length > 1 ? unit[0] : "";
  return mantissa * (PREFIX[prefix] ?? Number.NaN);
}

test("34465A default panel: function and range keys change the reading", async ({
  page,
}) => {
  await open(page, "benches/keysight-34465a.bench.yaml", "数字万用表", 4);
  await expect(page.getByTestId("panel-source")).toHaveAccessibleName(
    "定义的面板",
  ); // a hint, not display text (#281)
  const display = panel(page).getByTestId("panel-readout-meter.DC");
  // 1 V into 900 kΩ / (100 kΩ ∥ 10 MΩ): 99.103 mV on the 100 mV range the bench sets, within 4σ of
  // the reading noise (#1256: 0.1 µV at 10 PLC, √-scaled to 1 NPLC → σ ≈ 0.32 µV) and the 0.1 µV digit.
  await expectNear(display, 99.1035, 1.5e-3); // as shown, in mV
  // The display shows the range's digits (#1256): 6½ digits on 100 mV are "+099.dddd", in mV.
  await expect(display).toContainText("+099.");
  await expect
    .poll(async () =>
      ((await display.textContent()) ?? "")
        .split(".")
        .map((part) => part.length),
    )
    .toEqual([4, 4]);
  const range = panel(page).getByRole("group", { name: "量程设定" });
  await expect(range.getByRole("button", { name: "100 mV" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );

  // The 1000 V range is coarser and noisier (#1256: 0.1 ppm of range at 10 PLC, √-scaled to 1 NPLC
  // → σ ≈ 0.32 mV): the same input reads 99.1 mV within 4σ and the 1 mV digit, shown in V on that range.
  await range.getByRole("button", { name: "1000 V" }).click();
  await expect(range.getByRole("button", { name: "1000 V" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect
    .poll(async () => Math.abs((await readoutValue(display)) - 0.099103))
    .toBeLessThanOrEqual(4 * 0.32e-3 + 1e-3);
  await expect(display).toContainText("+0000.");
  await expect
    .poll(
      async () => ((await display.textContent()) ?? "").split(".")[1]?.length,
    )
    .toBe(3); // no longer to 1 µV
  await range.getByRole("button", { name: "AUTO" }).click();
  await expect(range.getByRole("button", { name: "AUTO" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(
    panel(page).getByRole("switch", { name: "自动量程" }),
  ).toHaveCount(0); // the AUTO key is the autorange

  // AC: the main display shows the AC reading, 0 V for a DC input.
  const fn = panel(page).getByRole("group", { name: "测量功能" });
  await fn.getByRole("button", { name: "AC V" }).click();
  await expect(fn.getByRole("button", { name: "AC V" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(panel(page).getByTestId("panel-readout-meter.DC")).toHaveCount(
    0,
  );
  // Only the reading noise is left (#1256: σ ≈ 0.32 µV on this range at 1 NPLC), far below a real AC
  // signal; an rms reading is never negative (#803).
  const ac = panel(page).getByTestId("panel-readout-meter.AC");
  await expect.poll(async () => readoutValue(ac)).toBeGreaterThanOrEqual(0);
  await expect
    .poll(async () => readoutValue(ac))
    .toBeLessThan(4 * 0.32e-6 + 1e-7);

  // NPLC and input impedance are on the panel too, for the DC voltage they apply to (neither is
  // shown for AC, the declared panel's `when`).
  await expect(
    panel(page).getByRole("textbox", { name: "积分时间（PLC）" }),
  ).toHaveCount(0);
  await fn.getByRole("button", { name: "DC V" }).click();
  await expect(
    panel(page).getByRole("textbox", { name: "积分时间（PLC）" }),
  ).toBeVisible();
  await expect(
    panel(page).getByRole("switch", { name: "输入阻抗自动（10 MΩ ↔ >10 GΩ）" }),
  ).toBeVisible();
});

test("5522A default panel: set the output, operate and standby", async ({
  page,
}) => {
  await open(page, "benches/fluke-5522a-dmm.bench.yaml", "5522A", 2);
  const terminal = panel(page).getByTestId("panel-readout-out.output");
  await expect(terminal).toHaveText("STBY");

  const level = panel(page).getByRole("textbox", { name: "幅度设定" });
  await level.fill("2.5");
  await level.press("Enter");
  await expect(panel(page).getByTestId("panel-readout-out.level")).toHaveText(
    "2.500000",
  );

  const keys = panel(page).getByRole("group", { name: "工作" });
  await keys.getByRole("button", { name: "OPER" }).click();
  await expect(terminal).toHaveText("OPER");
  await expect(keys.getByRole("button", { name: "OPER" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(keys.getByRole("button", { name: "STBY" })).toHaveAttribute(
    "aria-pressed",
    "false",
  );
  // After the 5 s settling time the meter on the output reads it (within the hidden deviation).
  await page.getByRole("combobox", { name: "推进" }).click();
  await page.getByRole("option", { name: "1 s" }).click();
  for (let i = 0; i < 6; i++)
    await page.getByRole("button", { name: "推进" }).click();
  await page.getByRole("tab", { name: "画布" }).click();
  await page
    .locator(".react-flow__node")
    .filter({ hasText: "SB-DMM-1" })
    .dblclick();
  await expectNear(
    panel(page).getByTestId("panel-readout-meter.DC"),
    2.5,
    1e-4,
  );

  await page.getByRole("tab", { name: "cal" }).click();
  await keys.getByRole("button", { name: "STBY" }).click();
  await expect(terminal).toHaveText("STBY");
  await expect(keys.getByRole("button", { name: "STBY" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  // ACV puts the frequency on the display.
  await expect(
    panel(page).getByTestId("panel-readout-out.frequency"),
  ).toHaveCount(0);
  await panel(page)
    .getByRole("group", { name: "功能" })
    .getByRole("button", { name: "ACV" })
    .click();
  await expect(
    panel(page).getByTestId("panel-readout-out.frequency"),
  ).toBeVisible();
});

test("a declared panel: its groups, controls and softkeys", async ({
  page,
}) => {
  await open(page, "benches/panel.bench.yaml", "面板万用表", 2);
  await expect(page.getByTestId("panel-source")).toHaveAccessibleName(
    "定义的面板",
  ); // a hint, not display text (#281)
  for (const name of ["功能", "量程", "高级"])
    await expect(panel(page).getByRole("region", { name })).toBeVisible();
  await expectNear(
    panel(page).getByTestId("panel-readout-meter.DC"),
    5.0,
    0.01,
  );

  // Buttons, a number with step keys, a drop-down and a switch, as declared.
  await panel(page)
    .getByRole("group", { name: "量程" })
    .getByRole("button", { name: "1 V" })
    .click();
  await expect(panel(page).getByTestId("panel-readout-meter.DC")).toHaveText(
    "OVLD",
  );
  const nplc = panel(page).getByRole("textbox", { name: "NPLC" });
  await expect(nplc).toHaveValue("1");
  await panel(page).getByRole("button", { name: "NPLC 增大一步" }).click();
  await expect(nplc).toHaveValue("1.5");
  await expect(
    panel(page).getByRole("combobox", { name: "输入阻抗" }),
  ).toBeVisible();
  await expect(
    panel(page).getByRole("switch", { name: "蜂鸣器" }),
  ).toBeChecked();

  const soft = panel(page).getByRole("group", { name: "软键" });
  await soft.getByRole("button", { name: "精确" }).click();
  await expect(soft.getByRole("button", { name: "精确" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(nplc).toHaveValue("10");
});

test("in build mode the panel sets the bench file's initial settings", async ({
  page,
}) => {
  const path = `e2e/panel-${Date.now()}.bench.yaml`;
  const source = await page.request.get(
    `/api/benches/file?path=${encodeURIComponent("benches/panel.bench.yaml")}`,
    { headers },
  );
  const data = (
    (await source.json()) as {
      data: { instruments: { awg: { state: Record<string, unknown> } } };
    }
  ).data;
  data.instruments.awg.state["ch1.load"] = "INF"; // JSON has no infinity: the file's .inf reads back as null
  const put = await page.request.put("/api/benches/file", {
    headers,
    data: { path, base_sha256: null, data },
  });
  expect(put.ok()).toBeTruthy();
  const sid = await open(page, path, "面板万用表", 2);

  await page.getByRole("radio", { name: "搭建" }).click();
  await page.getByRole("tab", { name: "面板万用表" }).click(); // build mode opens on the canvas
  await expect(page.getByText("面板设置的是初始设置")).toBeVisible();
  const range = panel(page).getByRole("group", { name: "量程" });
  await range.getByRole("button", { name: "10 V" }).click();
  await expect(range.getByRole("button", { name: "10 V" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(page.getByText("已保存到文件")).toBeVisible();

  // The file has the key's setting and the autorange it turns off; the running meter is unchanged.
  const doc = await page.request.get(
    `/api/benches/file?path=${encodeURIComponent(path)}`,
    { headers },
  );
  const instruments = (
    (await doc.json()) as {
      data: {
        instruments: Record<string, { state?: Record<string, unknown> }>;
      };
    }
  ).data.instruments;
  expect(instruments.dmm.state).toEqual({
    "sense.range": 10,
    "sense.range_auto": false,
  });
  const info = await page.request.get(`/api/sessions/${sid}`, { headers });
  const dmm = (
    (await info.json()) as {
      instruments: {
        name: string;
        state: { path: string; value: unknown }[];
      }[];
    }
  ).instruments.find((i) => i.name === "dmm");
  expect(dmm?.state.find((v) => v.path === "sense.range_auto")?.value).toBe(
    true,
  );
  await expect(
    page
      .getByText("量程：0 V（初始：10 V）")
      .or(page.getByText("自动量程：开（初始：关）"))
      .first(),
  ).toBeVisible();
});

test("the stepper is unavailable while covered, read-only, or disconnected (#742)", async ({
  page,
}) => {
  const start = await page.request.post("/api/sessions", {
    headers,
    data: { bench_path: "benches/panel.bench.yaml" },
  });
  const sid = ((await start.json()) as { id: string }).id;
  await page.goto(`/s/${sid}?token=${TOKEN}`);
  await expect(page.locator(".react-flow__node")).toHaveCount(2);
  await page
    .locator(".react-flow__node")
    .filter({ hasText: "面板万用表" })
    .click();
  const range = page.getByRole("textbox", { name: "量程", exact: true });
  const value = async () => {
    const info = await page.request.get(
      `/api/sessions/${sid}/instruments/dmm`,
      { headers },
    );
    return (
      (await info.json()) as { state: { path: string; value: unknown }[] }
    ).state.find((v) => v.path === "sense.range")?.value;
  };

  // Automatic range covers the stored value: no arrows, and the keys do nothing.
  await expect(range).toBeEnabled(); // the field itself stays open; it shows 自动 instead
  await expect(range).toHaveValue("自动"); // the stored value is not the one in effect (#279)
  await expect(page.getByRole("button", { name: "增大 量程" })).toHaveCount(0);
  await range.focus();
  await page.keyboard.press("ArrowUp");
  await page.keyboard.press("ArrowDown");
  await page.mouse.wheel(0, -120);
  await page.waitForTimeout(300);
  expect(await value()).toBe(0.0);

  // With the automatic switch off the stepper works: type 1 V, then ↑ a tenth of the decade
  // (a continuous quantity: no 1-2-5 rungs without a declared ladder, model.ts stepNumber).
  await page.getByRole("switch", { name: "自动量程" }).click();
  await settledManual(page, sid, range);
  await expect(range).toHaveValue("0 V"); // the field settles on the stored value it now controls
  await range.fill("1");
  await range.press("Enter");
  await expect.poll(value).toBe(1.0);
  await range.focus();
  await page.keyboard.press("ArrowUp");
  await expect(range).toHaveValue("1.1 V");
  await expect.poll(value).toBe(1.1);

  // A read-only setting is a disabled field without arrows.
  await expect(page.getByRole("textbox", { name: "校准常数" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "增大 校准常数" })).toHaveCount(
    0,
  );

  // Disconnected (the session stops): the next poll's 404 replaces the workbench with the
  // stopped-bench state (#877) — there is no steppable field left to reach.
  const stop = await page.request.delete(`/api/sessions/${sid}`, { headers });
  expect(stop.status()).toBe(204);
  await expect(page.getByTestId("bench-stopped")).toBeVisible({
    timeout: 10_000,
  });
  await expect(page.getByText("测试台已停止")).toBeVisible();
  await expect(range).toHaveCount(0);
});

/** After the autorange switch is turned off: the meter has stored it and the panel shows it (the
 * switch off, the range field enabled), so a value typed next is not replaced by the refresh that
 * the switch's write brings (a race under CI load). */
async function settledManual(
  page: Page,
  sid: string,
  range: Locator,
): Promise<void> {
  const auto = () =>
    page.request
      .get(`/api/sessions/${sid}/instruments/dmm`, { headers })
      .then((r) => r.json())
      .then(
        (d) =>
          (d as { state: { path: string; value: unknown }[] }).state.find(
            (v) => v.path === "sense.range_auto",
          )?.value,
      );
  await expect.poll(auto).toBe(false);
  await expect(
    page.getByRole("switch", { name: "自动量程" }),
  ).not.toBeChecked();
  await expect(range).toBeEnabled();
}

test("Escape leaves a field's draft unwritten (#758)", async ({ page }) => {
  const start = await page.request.post("/api/sessions", {
    headers,
    data: { bench_path: "benches/panel.bench.yaml" },
  });
  const sid = ((await start.json()) as { id: string }).id;
  await page.goto(`/s/${sid}?token=${TOKEN}`);
  await expect(page.locator(".react-flow__node")).toHaveCount(2);
  await page
    .locator(".react-flow__node")
    .filter({ hasText: "面板万用表" })
    .click();
  const range = page.getByRole("textbox", { name: "量程", exact: true });
  await page.getByRole("switch", { name: "自动量程" }).click();
  await settledManual(page, sid, range);
  await expect(range).toHaveValue("0 V");
  await range.fill("1");
  await range.press("Enter");
  const value = () =>
    page.request
      .get(`/api/sessions/${sid}/instruments/dmm`, { headers })
      .then((r) => r.json())
      .then(
        (d) =>
          (
            (d as { state: { path: string; value: unknown }[] }).state.find(
              (v) => v.path === "sense.range",
            ) as { value: number }
          ).value,
      );
  await expect.poll(value).toBe(1.0);
  // type a draft and cancel it with Escape: nothing reaches the instrument
  await range.focus();
  await range.fill("55");
  await range.press("Escape");
  await expect(range).toHaveValue("1 V");
  await page.waitForTimeout(400);
  expect(await value()).toBe(1.0);
});
