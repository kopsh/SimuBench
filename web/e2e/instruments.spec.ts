import { expect, test } from "@playwright/test";

const TOKEN = "e2e-token";

test("lock-in screen: readouts follow the circuit and settle after a change", async ({ page }) => {
  await page.goto(`/?token=${TOKEN}`);
  await page.getByRole("listitem").filter({ hasText: "lockin_rc" }).getByRole("button", { name: /^(打开测试台|进入)$/ }).click();
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

test("spectrum analyzer screen: peak search walks the harmonics", async ({ page }) => {
  await page.goto(`/?token=${TOKEN}`);
  await page.getByRole("listitem").filter({ hasText: /^specan/ }).getByRole("button", { name: /^(打开测试台|进入)$/ }).click();
  await expect(page.locator(".react-flow__node")).toHaveCount(3);
  await page.locator(".react-flow__node").filter({ hasText: "频谱仪" }).dblclick();
  const threshold = page.getByRole("textbox", { name: "峰值门限" }).first();
  await threshold.fill("-30");
  await threshold.press("Enter");
  await page.getByRole("button", { name: "峰值搜索" }).click();
  // 1 Vpp square into 50 Ω: 0.5 V peak, fundamental 4·0.5/π V → +6.0778 dBm (D13); the sweep's
  // noise sample moves the reading by about ±0.0002 dB, across the display's rounding edge.
  await expect(page.getByTestId("marker-readout")).toHaveText(/100 kHz · 6\.07[78] dBm/);
  await page.getByRole("button", { name: "下一峰值" }).click();
  await expect(page.getByTestId("marker-readout")).toHaveText(/300 kHz · -3\.46\d dBm/);
  await page.getByRole("textbox", { name: "跨度" }).first().fill("10M");
  await page.getByRole("textbox", { name: "跨度" }).first().press("Enter");
  await expect(page.getByText("RBW 100 kHz")).toBeVisible(); // the analyzer's coupling, not the UI's
});

test("SMU screen: the limit takes over, a sweep fills the I–V curve and the buffer", async ({ page }) => {
  await page.goto(`/?token=${TOKEN}`);
  await page.getByRole("listitem").filter({ hasText: /^smu/ }).getByRole("button", { name: /^(打开测试台|进入)$/ }).click();
  await expect(page.locator(".react-flow__node")).toHaveCount(3);
  await page.locator(".react-flow__node").filter({ hasText: "源测量单元" }).dblclick();
  // 5 V into 1 kΩ ∥ 10 MΩ = 999.9 Ω
  await expect(page.getByTestId("smu-regulation")).toHaveText("CV");
  await expect(page.getByTestId("readout-I")).toHaveText(/^5\.0005/);

  const limit = page.getByRole("textbox", { name: "电流限值" }).first();
  await limit.fill("4m");
  await limit.press("Enter");
  await expect(page.getByTestId("smu-regulation")).toHaveText("CC");
  await expect(page.getByTestId("readout-V")).toHaveText(/^3\.9996/);

  const console = page.getByRole("textbox", { name: "发送 SCPI 命令" });
  await console.fill(":READ?");
  await console.press("Enter");
  await expect(page.getByRole("status", { name: "响应" })).toContainText("+0.0000000E+00");
  const rows = page.getByTestId("smu-buffer").locator("tbody tr");
  await expect(rows).toHaveCount(9); // the configured 0–8 V sweep; from 4 V on the limit holds
  await expect(rows.nth(8)).toContainText("3.9996");
  await expect(page.getByRole("img", { name: "I–V 曲线（缓冲区）" })).toBeVisible();

  // The same from the screen's own keys, without a script or the console.
  const keys = page.getByRole("group", { name: "测量控制" });
  await keys.getByRole("button", { name: "清空缓冲区" }).click();
  await expect(page.getByText("缓冲区为空").first()).toBeVisible();
  await keys.getByRole("button", { name: "单次读数" }).click();
  await expect(rows).toHaveCount(1); // one reading at the source level, whatever the sweep
  await keys.getByRole("button", { name: "执行" }).click();
  await expect(rows).toHaveCount(9);
});

test("wall clock: time runs by itself and the lock-in settles without advancing", async ({ page }) => {
  const headers = { "x-simubench-token": TOKEN };
  const path = `e2e/wall-${Date.now()}.bench.yaml`;
  const data = {
    schema: "simubench.bench/1",
    name: "wall",
    instruments: {
      lia: { model: "sb-lia-1", label: "锁相放大器", state: { "reference.frequency": 1515.76, "sine_out.amplitude": 0.5, "filter.time_constant": 0.03 } },
    },
    duts: { rc: { type: "rc_lowpass", params: { r: 1000, c: 1e-7 } } },
    wires: [
      { from: "lia.SINE_OUT", to: "rc.IN" },
      { from: "rc.OUT", to: "lia.SIG_IN" },
    ],
    run: { clock: "wall", seed: 1 },
  };
  expect((await page.request.put("/api/benches/file", { headers, data: { path, base_sha256: null, data } })).ok()).toBeTruthy();
  const sid = ((await (await page.request.post("/api/sessions", { headers, data: { bench_path: path } })).json()) as { id: string }).id;
  await page.goto(`/s/${sid}?token=${TOKEN}`);
  await expect(page.getByText("墙钟", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /推进/ })).toHaveCount(0); // nothing to advance
  const clock = page.getByTitle("虚拟时间", { exact: true });
  const first = await clock.textContent();
  await expect(clock).not.toHaveText(first ?? "");

  await page.locator(".react-flow__node").filter({ hasText: "锁相放大器" }).dblclick();
  await expect(page.getByTestId("readout-R")).toHaveText(/^353\.5/);
  const freq = page.getByRole("textbox", { name: "参考频率" }).first();
  await freq.fill("100");
  await freq.press("Enter");
  // Real time passes; a 30 ms, 24 dB/oct filter settles well within a few seconds.
  await expect(page.getByTestId("readout-R")).toHaveText(/^498\.8/, { timeout: 10_000 });
});

test("fidelity: a wire whose signal is truncated is marked ≈ with the reason", async ({ page }) => {
  const headers = { "x-simubench-token": TOKEN };
  const path = `e2e/approx-${Date.now()}.bench.yaml`;
  const data = {
    schema: "simubench.bench/1",
    name: "approx",
    instruments: {
      sig: { model: "sb-awg-2", state: { "ch1.function": "SQUare", "ch1.frequency": 1000, "ch1.output": true, "ch1.load": "INF" } },
      lia: { model: "sb-lia-1" },
    },
    wires: [{ from: "sig.OUT1", to: "lia.SIG_IN" }],
  };
  expect((await page.request.put("/api/benches/file", { headers, data: { path, base_sha256: null, data } })).ok()).toBeTruthy();
  const sid = ((await (await page.request.post("/api/sessions", { headers, data: { bench_path: path } })).json()) as { id: string }).id;
  await page.goto(`/s/${sid}?token=${TOKEN}`);
  const mark = page.getByTestId("wire-approx");
  await expect(mark).toHaveText("≈");
  await expect(mark).toHaveAttribute("title", /square wave: \d+ harmonics/);
});
