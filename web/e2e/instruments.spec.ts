import { expect, type Locator, test } from "@playwright/test";

import { benchRow, expectAttributeContains, expectNear, expectText, isDigits, isHome, openButton, quantity } from "./judge";

const TOKEN = "e2e-token";

test("lock-in screen: readouts follow the circuit and settle after a change", async ({ page }) => {
  await page.goto(`/?token=${TOKEN}`);
  await openButton(benchRow(page, "lockin_rc")).click();
  await expect(page.locator(".react-flow__node")).toHaveCount(3);
  await page.locator(".react-flow__node").filter({ hasText: "锁相放大器" }).dblclick();
  await expect(page.getByText("已锁定")).toBeVisible();
  // 0.5 Vrms at the physics-correct corner 1515.76 Hz: R = 353.51 mV, θ = −44.99° (the readouts
  // as shown, in mV and degrees; the band is the display's last digits).
  await expectNear(page.getByTestId("readout-R"), 353.51, 0.05);
  await expectNear(page.getByTestId("readout-θ"), -44.99, 0.01);

  const freq = page.getByRole("textbox", { name: "参考频率" }).first();
  await freq.fill("100");
  await freq.press("Enter");
  // Right after the change the output is still near the old value; after advancing it settles.
  await expectNear(page.getByTestId("readout-R"), 353.51, 0.05);
  for (let i = 0; i < 4; i++) {
    await page.getByRole("button", { name: "推进" }).click();
  }
  await page.getByRole("combobox", { name: "推进" }).click();
  await page.getByRole("option", { name: "1 s" }).click();
  await page.getByRole("button", { name: "推进" }).click();
  await expectNear(page.getByTestId("readout-R"), 498.85, 0.05);
});

test("spectrum analyzer screen: peak search walks the harmonics", async ({ page }) => {
  await page.goto(`/?token=${TOKEN}`);
  await openButton(benchRow(page, "specan")).click();
  await expect(page.locator(".react-flow__node")).toHaveCount(3);
  await page.locator(".react-flow__node").filter({ hasText: "频谱仪" }).dblclick();
  const threshold = page.getByRole("textbox", { name: "峰值门限" }).first();
  await threshold.fill("-30");
  await threshold.press("Enter");
  await page.getByRole("button", { name: "峰值搜索" }).click();
  // 1 Vpp square into 50 Ω: 0.5 V peak, fundamental 4·0.5/π V → +6.0778 dBm (D13); the sweep's
  // noise sample moves the reading by about ±0.0002 dB, across the display's rounding edge.
  await expectMarker(page.getByTestId("marker-readout"), "100 kHz", 6.0778, 0.001);
  await page.getByRole("button", { name: "下一峰值" }).click();
  await expectMarker(page.getByTestId("marker-readout"), "300 kHz", -3.465, 0.005);
  await page.getByRole("textbox", { name: "跨度" }).first().fill("10M");
  await page.getByRole("textbox", { name: "跨度" }).first().press("Enter");
  await expect(page.getByText("RBW 100 kHz")).toBeVisible(); // the analyzer's coupling, not the UI's
});

test("spectrum analyzer: power-on shows no UNCAL and no stale manual RBW; a short sweep time does", async ({ page }) => {
  await page.goto(`/?token=${TOKEN}`);
  const specan = benchRow(page, "specan");
  await openButton(specan).click();
  await expect(page.locator(".react-flow__node")).toHaveCount(3);
  // A new session at power-on (#345): the service is shared by the tests, and the peak-search
  // test left a 10 MHz span on this bench, so close it and open the bench again.
  await page.getByRole("button", { name: "关闭测试台" }).click();
  await expect(page).toHaveURL(isHome);
  await specan.getByRole("button", { name: "打开测试台" }).click();
  await expect(page.locator(".react-flow__node")).toHaveCount(3);
  const sid = page.url().split("/s/")[1]?.split("?")[0];
  const scpi = async (command: string) =>
    ((await (
      await page.request.post(`/api/sessions/${sid}/instruments/sa/scpi`, {
        headers: { "x-simubench-token": TOKEN },
        data: { command },
      })
    ).json()) as { response: string }).response.trim();
  expect(await scpi(":SWE:TIME:UNC?")).toBe("0");
  const rbwHz = Number(await scpi(":BAND?"));

  await page.locator(".react-flow__node").filter({ hasText: "频谱仪" }).dblclick();
  // Power-on state: RBW, VBW and sweep time are coupled. The screen reads the coupled RBW the
  // instrument reports, and the inspector does not put the stored manual 3 MHz beside it (#279).
  await expect(page.getByText(`RBW ${rbwHz / 1e3} kHz`)).toBeVisible();
  await expect(page.getByTestId("uncal")).toHaveCount(0);
  const manualRbw = page.getByRole("textbox", { name: "分辨率带宽 (手动)" }).first();
  await expect(manualRbw).toHaveValue("自动");
  await expect(page.getByText("自动时不生效").first()).toBeVisible();
  await expect(page.getByText("3 MHz")).toHaveCount(0);

  // 1 ms is shorter than the coupled RBW needs over this span: the amplitudes are uncalibrated.
  const sweep = page.getByRole("textbox", { name: "扫描时间" }).first();
  await sweep.fill("1m");
  await sweep.press("Enter");
  await expect(page.getByTestId("uncal")).toBeVisible();
  await expectAttributeContains(page.getByTestId("uncal"), "title", "扫描时间短于");
});

test("SMU screen: the limit takes over, a sweep fills the I–V curve and the buffer", async ({ page }) => {
  await page.goto(`/?token=${TOKEN}`);
  await openButton(benchRow(page, "smu")).click();
  await expect(page.locator(".react-flow__node")).toHaveCount(3);
  await page.locator(".react-flow__node").filter({ hasText: "源测量单元" }).dblclick();
  // 5 V into 1 kΩ ∥ 10 MΩ = 999.9 Ω
  await expect(page.getByTestId("smu-regulation")).toHaveText("CV");
  await expectNear(page.getByTestId("readout-I"), 5.0005, 1e-4); // 5.0005 mA, signed as the SMU display shows it
  await expectText(page.getByTestId("readout-I"), (t) => t.startsWith("+"), "a signed reading");

  const limit = page.getByRole("textbox", { name: "电流限值" }).first();
  await limit.fill("4m");
  await limit.press("Enter");
  await expect(page.getByTestId("smu-regulation")).toHaveText("CC");
  await expectNear(page.getByTestId("readout-V"), 3.9996, 1e-4); // 4 mA · 999.9 Ω

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
  await expect(page.getByRole("button", { name: "推进" })).toHaveCount(0); // nothing to advance
  const clock = page.getByTitle("虚拟时间", { exact: true });
  const first = await clock.textContent();
  await expect(clock).not.toHaveText(first ?? "");

  await page.locator(".react-flow__node").filter({ hasText: "锁相放大器" }).dblclick();
  await expectNear(page.getByTestId("readout-R"), 353.51, 0.05);
  const freq = page.getByRole("textbox", { name: "参考频率" }).first();
  await freq.fill("100");
  await freq.press("Enter");
  // Real time passes; a 30 ms, 24 dB/oct filter settles well within a few seconds.
  await expectNear(page.getByTestId("readout-R"), 498.85, 0.05, "", { timeout: 10_000 });
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
  await expectAttributeContains(mark, "title", "square wave: ");
  const title = (await mark.getAttribute("title")) ?? "";
  const count = title.split("square wave: ")[1]?.split(" ");
  expect(count && isDigits(count[0]) && count[1] === "harmonics", title).toBeTruthy();
});

test("scope screen: the measurement table matches :MEASure? on the record shown, and cursors read Δt/ΔV", async ({ page }) => {
  const headers = { "x-simubench-token": TOKEN };
  const start = await page.request.post("/api/sessions", {
    headers,
    data: { bench_path: "benches/keysight-dsox2024a.bench.yaml", force: true },
  });
  expect(start.ok(), await start.text()).toBeTruthy();
  const sid = ((await start.json()) as { id: string }).id;
  const scpi = async (command: string) =>
    ((await (
      await page.request.post(`/api/sessions/${sid}/instruments/scope/scpi`, { headers, data: { command } })
    ).json()) as { response: string | null }).response?.trim() ?? "";
  // :DIGitize takes one acquisition and stops (#248): the screen then shows that record.
  await scpi(":DIGitize CHANnel1");
  const vpp = Number(await scpi(":MEASure:VPP? CHANnel1"));
  const freq = Number(await scpi(":MEASure:FREQuency? CHANnel1"));
  expect(freq).toBeGreaterThan(900);

  await page.goto(`/s/${sid}?token=${TOKEN}`);
  await expect(page.locator(".react-flow__node")).toHaveCount(3);
  await page.locator(".react-flow__node").filter({ hasText: "示波器" }).dblclick();
  await expect(page.getByTestId("scope-source")).toContainText("采集次数"); // a record, not live frames
  const cellValue = async (id: string) => {
    const text = (await page.getByTestId(id).textContent()) ?? "";
    return quantity(text, id.endsWith("vpp") ? "V" : "Hz") ?? NaN;
  };
  await expect(page.getByTestId("meas-ch1-vpp")).not.toHaveText("—");
  // The table shows four significant digits: a relative error of at most half a unit in the
  // fourth digit (5e-4); 1e-3 leaves room for the rounding of the reply itself (#356).
  expect(Math.abs((await cellValue("meas-ch1-vpp")) - vpp) / vpp).toBeLessThan(1e-3);
  expect(Math.abs((await cellValue("meas-ch1-frequency")) - freq) / freq).toBeLessThan(1e-3);

  // Cursors: 6 divisions apart at 200 µs/div is 1.2 ms; 4 divisions at 500 mV/div is 2 V.
  await page.getByRole("button", { name: "光标" }).click();
  await expect(page.getByTestId("scope-cursors")).toBeVisible();
  await expectNear(page.getByTestId("cursor-dt"), 1.2e-3, 1e-7, "s");
  await expectNear(page.getByTestId("cursor-dv"), 2, 1e-4, "V");
  await expect(page.getByTestId("cursor-readout")).toContainText("1.2");

  // Drag X2 on the screen to the middle (5 div): 3 divisions from X1 at 200 µs/div is 600 µs (#356).
  const box = (await page.getByTestId("scope-cursors").boundingBox())!;
  const handle = (await page.getByTestId("cursor-x2").boundingBox())!;
  await page.mouse.move(handle.x + handle.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 5 });
  await page.mouse.up();
  await expectNear(page.getByTestId("cursor-dt"), 600e-6, 1e-8, "s");
  await expect(page.getByTestId("cursor-readout")).toContainText("600");
});

/** A marker readout `MKR <frequency> · <level> dBm`: the frequency as written, the level in its band. */
async function expectMarker(readout: Locator, frequency: string, dbm: number, tol: number) {
  await expectText(
    readout,
    (text) => {
      const [f, level] = text.split(" · ");
      const v = quantity(level ?? "", "dBm");
      return (f ?? "").trim().split(" ").slice(-2).join(" ") === frequency && v !== null && Math.abs(v - dbm) <= tol;
    },
    `${frequency} · ${dbm} ± ${tol} dBm`,
  );
}
