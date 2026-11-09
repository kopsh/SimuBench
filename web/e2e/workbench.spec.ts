import { expect, type Page, test } from "@playwright/test";

const TOKEN = "e2e-token";

async function startBench(page: Page) {
  await page.goto(`/?token=${TOKEN}`);
  await expect(
    page.getByRole("heading", { name: "测试台", exact: true, level: 1 }),
  ).toBeVisible();
  await page
    .getByRole("listitem")
    .filter({ hasText: "awg_rc_scope" })
    .getByRole("button", { name: /^(打开测试台|进入)$/ })
    .click();
  await expect(page).toHaveURL(/\/s\/[0-9a-f]+$/);
  await expect(page.locator(".react-flow__node")).toHaveCount(3);
}

test("the token is removed from the address bar", async ({ page }) => {
  await page.goto(`/?token=${TOKEN}`);
  await expect(
    page.getByRole("heading", { name: "测试台", exact: true, level: 1 }),
  ).toBeVisible();
  expect(page.url()).not.toContain("token");
});

test("a wrong token explains what to do", async ({ browser }) => {
  const page = await (await browser.newContext()).newPage();
  await page.goto("/?token=wrong");
  await expect(page.getByText("访问令牌无效")).toBeVisible();
});

test("start a bench, inspect and edit an instrument", async ({ page }) => {
  await startBench(page);
  await page
    .locator(".react-flow__node")
    .filter({ hasText: "函数发生器" })
    .click();
  const inspector = page
    .getByText("身份与地址")
    .locator("xpath=ancestor::div[contains(@class,'space-y-4')]");
  await expect(
    inspector.getByText("TCPIP0::192.168.1.20::5025::SOCKET"),
  ).toBeVisible();

  const freq = page.getByRole("textbox", { name: "频率" }).first();
  await freq.fill("2.5k");
  await freq.press("Enter");
  await expect(
    page.locator(".react-flow__node").filter({ hasText: "函数发生器" }),
  ).toContainText("2.5 kHz");

  // Out of range: the change is refused and the field explains why.
  await freq.fill("1G");
  await freq.press("Enter");
  await expect(page.getByLabel(/-222/)).toBeVisible();
});

test("SCPI console, communication log and problems", async ({ page }) => {
  await startBench(page);
  await page.getByRole("button", { name: "示波器" }).first().click();
  const input = page.getByRole("textbox", { name: "发送 SCPI 命令" });
  await input.fill("*IDN?");
  await input.press("Enter");
  await expect(page.getByText(/SIMUBENCH,SB-DSO-4/).first()).toBeVisible();
  await input.fill(":CHANnel9:SCALe 1");
  await input.press("Enter");
  await expect(page.getByRole("tab", { name: /问题/ })).toContainText("1");
  await page.getByRole("tab", { name: /问题/ }).click();
  await expect(
    page.getByRole("button", { name: /-114.*Header suffix out of range/ }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "通信" }).click();
  const row = page.getByText(":CHANnel9:SCALe 1").locator("..");
  await expect(row).toBeVisible();
  await expect(row).toContainText("界面"); // who sent it
  await expect(row.getByTestId("wall-time")).toHaveText(
    /^\d\d:\d\d:\d\d\.\d{3}$/,
  ); // real time beside virtual time
});

test("open the scope screen by double-clicking its node", async ({ page }) => {
  await startBench(page);
  await page
    .locator(".react-flow__node")
    .filter({ hasText: "示波器" })
    .dblclick();
  await expect(page.getByRole("tab", { name: /示波器/ })).toBeVisible();
  await expect(page.getByRole("img", { name: "waveform" })).toBeVisible();
  await expect(page.getByText("运行中", { exact: true })).toBeVisible(); // a scope runs from power-on
});

test("the scope's run, stop and single are front-panel buttons, not SCPI", async ({
  page,
}) => {
  await startBench(page);
  const controls = page.getByRole("group", { name: "采集控制" });
  await page
    .locator(".react-flow__node")
    .filter({ hasText: "示波器" })
    .dblclick();
  // Outputs are off at power-on: the notice traces CH1 back to the generator, through the probe and the RC.
  const why = page.getByRole("status", { name: "输入为 0 V 的原因" });
  await expect(why).toContainText("探头 10×");
  await expect(why).toContainText("RC 低通");
  await expect(why).toContainText("函数发生器 OUT1 输出关闭");
  await controls.getByRole("button", { name: "停止" }).click();
  await expect(page.getByText("已停止", { exact: true })).toBeVisible();
  await expect(page.getByText("还没有采集")).toBeVisible(); // stopped: the screen holds the last acquisition
  await controls.getByRole("button", { name: "单次" }).click();
  await expect(page.getByText("还没有采集")).toBeHidden();
  await expect(page.getByText(/采集次数\s*1/)).toBeVisible();
  await controls.getByRole("button", { name: "运行" }).click();
  await expect(page.getByText("运行中", { exact: true })).toBeVisible();
  await page.waitForTimeout(1000); // the screen redraws while running: frames, not acquisitions
  await expect(page.getByTestId("scope-source")).toHaveText("实时画面"); // said so, not a count (#280)
  await expect(page.getByText(":SINGle")).toHaveCount(0); // nothing in the communication log
  await page.getByRole("tab", { name: "事件" }).click();
  await expect(page.getByText(/scope 面板: 单次/)).toBeVisible();
  await expect(page.getByText(/scope 面板: 运行/)).toBeVisible();
});

test("an unwired channel says so; clicking the source selects it", async ({
  page,
}) => {
  await startBench(page);
  await page
    .locator(".react-flow__node")
    .filter({ hasText: "示波器" })
    .dblclick();
  const input = page.getByRole("textbox", { name: "发送 SCPI 命令" });
  await input.fill(":CHANnel2:DISPlay ON");
  await input.press("Enter");
  const why = page.getByRole("status", { name: "输入为 0 V 的原因" });
  await expect(why).toContainText("没有接线");
  await page.getByRole("button", { name: "函数发生器 OUT1 输出关闭" }).click();
  await expect(
    page.getByText("TCPIP0::192.168.1.20::5025::SOCKET"),
  ).toBeVisible(); // the generator is selected
});

test("live canvas: wires and thumbnails follow the signal, nodes flash on commands", async ({
  page,
}) => {
  await startBench(page);
  const sid = new URL(page.url()).pathname.split("/").pop()!;
  // Output off at power-on: every wire silent, the scope thumbnail flat, the clock waiting.
  await expect(page.locator('path[data-level="silent"]')).toHaveCount(2);
  await expect(page.getByRole("img", { name: "CH1 0 V" })).toBeVisible();
  await expect(page.getByTestId("session-activity")).toContainText("等待命令");
  await page.request.post(`/api/sessions/${sid}/instruments/awg/scpi`, {
    headers: { "x-simubench-token": TOKEN },
    data: { command: ":OUTPut1 ON" },
  });
  await expect(page.locator('path[data-level="live"]')).toHaveCount(2);
  await expect(page.getByRole("img", { name: "CH1 signal" })).toBeVisible();
  await expect(
    page
      .locator(".react-flow__node")
      .filter({ hasText: "函数发生器" })
      .getByTestId("node-activity"),
  ).toHaveCount(1);
  await expect(page.getByTestId("session-activity")).toContainText("等待命令"); // back to waiting once it is done
});

test("real model: the Siglent scope's single is its own, inside the instrument", async ({
  page,
}) => {
  await page.goto(`/?token=${TOKEN}`);
  await page
    .getByRole("listitem")
    .filter({ hasText: "siglent_scope_rc" })
    .getByRole("button", { name: /^(打开测试台|进入)$/ })
    .click();
  await expect(page.locator(".react-flow__node")).toHaveCount(3);
  await expect(
    page.locator(".react-flow__node").filter({ hasText: "SDS2104X Plus" }),
  ).toBeVisible();
  await page
    .locator(".react-flow__node")
    .filter({ hasText: "示波器" })
    .dblclick();
  const controls = page.getByRole("group", { name: "采集控制" });
  await controls.getByRole("button", { name: "单次" }).click();
  await expect(page.getByText("等待触发", { exact: true })).toBeVisible(); // the generator is off: no trigger yet
  await controls.getByRole("button", { name: "运行" }).click();
  const sid = new URL(page.url()).pathname.split("/").pop()!;
  await page.request.post(`/api/sessions/${sid}/instruments/awg/scpi`, {
    headers: { "x-simubench-token": TOKEN },
    data: { command: "C1:OUTP ON" },
  });
  await controls.getByRole("button", { name: "单次" }).click();
  await expect(page.getByText("已停止", { exact: true })).toBeVisible(); // its single mode: one acquisition, then Stop
  // Run left it armed in its single mode: that acquisition triggered once the generator came on
  // (issue 134), and Single took one more.
  await expect(page.getByText(/采集次数\s*2/)).toBeVisible();
  await expect(page.getByText(":TRIGger:MODE SINGle")).toHaveCount(0); // run inside the scope, not over SCPI
  await page.getByRole("tab", { name: /问题/ }).click();
  await expect(page.getByText(/-113/)).toHaveCount(0); // the bench's probe warning is the only problem
});

test("open screens are remembered for the bench and reopen on the next start", async ({
  page,
}) => {
  await startBench(page);
  await page
    .locator(".react-flow__node")
    .filter({ hasText: "示波器" })
    .dblclick();
  await expect(page.getByRole("tab", { name: /示波器/ })).toBeVisible();
  await page.getByRole("button", { name: "返回测试台列表" }).click();
  // Start again: the scope screen comes back as the active tab, not the canvas.
  await page
    .getByRole("listitem")
    .filter({ hasText: "awg_rc_scope" })
    .getByRole("button", { name: /^(打开测试台|进入)$/ })
    .click();
  await expect(page).toHaveURL(/\/s\/[0-9a-f]+$/);
  await expect(page.getByRole("tab", { name: /示波器/ })).toBeVisible();
  await expect(page.getByRole("group", { name: "采集控制" })).toBeVisible();
});

test("switch to English and dark theme", async ({ page }) => {
  await startBench(page);
  await page.getByRole("button", { name: "设置" }).click();
  await page.getByRole("menuitemradio", { name: "English" }).click();
  await expect(
    page.getByText("Resources").or(page.getByText("Instruments")).first(),
  ).toBeVisible();
  await page.getByRole("button", { name: "Settings" }).click();
  await page.getByRole("menuitemradio", { name: "Dark" }).click();
  await expect(page.locator("html")).toHaveClass(/dark/);
});

test("run record: open it from the home page, see the timeline, replay identically", async ({
  page,
}) => {
  await page.goto(`/?token=${TOKEN}`);
  await page
    .getByRole("listitem")
    .filter({ hasText: "awg_rc_scope" })
    .getByRole("button", { name: /^(打开测试台|进入)$/ })
    .click();
  await expect(page.locator(".react-flow__node")).toHaveCount(3);
  const input = page.getByRole("textbox", { name: "发送 SCPI 命令" });
  await page.locator(".react-flow__node").filter({ hasText: "示波器" }).click();
  for (const cmd of [":DIGitize", ":WAVeform:DATA?", ":WAVeform:PREamble?"]) {
    await input.fill(cmd);
    await input.press("Enter");
    await expect(page.getByRole("status", { name: "响应" })).not.toBeEmpty();
  }

  // This session's record (other tests' sessions on the same bench write records too).
  const sid = new URL(page.url()).pathname.split("/").pop()!;
  const info = (await (
    await page.request.get(`/api/sessions/${sid}`, {
      headers: { "x-simubench-token": TOKEN },
    })
  ).json()) as {
    record_path: string;
  };
  const runId = info.record_path
    .split(/[\\/]/)
    .pop()!
    .replace(/^run-/, "")
    .replace(/\.jsonl$/, "");
  await page.getByRole("button", { name: "返回测试台列表" }).click();
  const runs = page.getByRole("region", { name: "记录", exact: true });
  await runs.locator(`a[href="/runs/${runId}"]`).click();
  await expect(page.getByTestId("run-timeline")).toBeVisible();
  const events = page.getByTestId("run-events");
  await expect(events).toContainText(":WAVeform:PREamble?");
  await expect(events).toContainText("由采集 #"); // the data block is kept as its recipe
  await page.getByRole("button", { name: "回放并比较" }).click();
  await expect(page.getByRole("status", { name: "回放结果" })).toContainText(
    "回放一致",
  );
});

test("virtual probe: select a net and see why it reads what it reads", async ({
  page,
}) => {
  await page.goto(`/?token=${TOKEN}`);
  await page
    .getByRole("listitem")
    .filter({ hasText: "awg_rc_scope" })
    .getByRole("button", { name: /^(打开测试台|进入)$/ })
    .click();
  await expect(page.locator(".react-flow__node")).toHaveCount(3);
  await page.getByRole("button", { name: /^net-1/ }).click();
  const probe = page.getByTestId("probe");
  await expect(probe).toContainText("没有信号源连到这个网络"); // the generator's output is off
  const input = page.getByRole("textbox", { name: "发送 SCPI 命令" });
  await page
    .locator(".react-flow__node")
    .filter({ hasText: "函数发生器" })
    .click();
  await input.fill(":OUTPut1:STATe ON");
  await input.press("Enter");
  await page.getByRole("button", { name: /^net-1/ }).click();
  await expect(page.getByTestId("probe-sources")).toContainText(
    "awg · ch1.out",
  );
  await expect(page.getByTestId("probe-sources")).toContainText("∠");
});

test("about and diagnostics: manifest, health checks, bundle download", async ({
  page,
}) => {
  await page.goto(`/?token=${TOKEN}`);
  await page.getByRole("button", { name: "设置" }).click();
  await page.getByRole("menuitem", { name: "关于与诊断" }).click();
  await expect(page.getByTestId("about-version")).toHaveText(/^\d+\.\d+\.\d+/);
  await expect(page.getByTestId("about-doctor")).toContainText(
    "pyvisa backend",
  );
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出诊断包" }).click();
  expect((await download).suggestedFilename()).toMatch(
    /^simubench-diagnostics-.*\.zip$/,
  );
});

test("first-use guide: three ways in, dismissed for good", async ({ page }) => {
  await page.goto(`/?token=${TOKEN}`);
  const guide = page.getByRole("region", { name: "开始使用 SimuBench" });
  await expect(guide).toBeVisible();
  await expect(guide).toContainText("@simubench");
  await expect(guide).toContainText("simubench mcp");
  await guide.getByRole("button", { name: "不再显示" }).click();
  await expect(guide).toBeHidden();
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "测试台", exact: true, level: 1 }),
  ).toBeVisible();
  await expect(
    page.getByRole("region", { name: "开始使用 SimuBench" }),
  ).toBeHidden();
});

test("instrument internals: the scope's components, wiring and panel ports, read-only (issue 20)", async ({
  page,
}) => {
  await page.goto(`/?token=${TOKEN}`);
  await page
    .getByRole("listitem")
    .filter({ hasText: "awg_rc_scope" })
    .getByRole("button", { name: /^(打开测试台|进入)$/ })
    .click();
  await page.locator(".react-flow__node").filter({ hasText: "示波器" }).click();
  await page.getByRole("button", { name: "查看内部结构" }).click();
  await expect(
    page.getByRole("tab", { name: /示波器 · 内部结构/ }),
  ).toBeVisible();
  const graph = page.getByTestId("internals-graph");
  // CH1 is open: input port -> vertical channel -> probe setting -> acquisition; the other channels
  // are collapsed groups (instrument-authoring.md 8.4).
  for (const name of ["ch1.inp", "ch1.front", "ch1.probe", "acq"])
    await expect(graph.locator(`[data-name="${name}"]`)).toBeVisible();
  await expect(graph.getByTestId("internal-group")).toHaveCount(3);
  await expect(graph.getByTestId("internal-port")).toHaveCount(4);
  // wires: CH1's chain (3) and its panel port, the groups to the acquisition (3) and their ports (3);
  // dependencies (#729): the time base, trigger and ADC group feed the acquisition, which feeds the
  // measurements and the transfer.
  await expect(graph.locator(".react-flow__edge")).toHaveCount(4 + 3 + 3 + 5);
  await expect(
    graph.locator('.react-flow__edge-path[style*="stroke-dasharray"]'),
  ).toHaveCount(5);
  for (const param of ["horizontal", "trigger", "adc", "record"])
    await expect(
      graph.locator(".react-flow__edge-text", { hasText: param }).first(),
    ).toBeVisible();
  await expect(graph.getByTestId("internal-command-side")).toHaveCount(0); // everything here is connected
  // A component's parameters: the bound ones follow the setting.
  await graph.locator('[data-name="ch1.front"]').click();
  const details = page.getByTestId("internal-details");
  await expect(details).toContainText("scope_vertical");
  await expect(details).toContainText("跟随设置：耦合");
  await expect(details).toContainText("200 MHz");
  // Expanding everything shows every channel's chain.
  await page.getByRole("button", { name: "全部展开" }).click();
  // 4 channels × (input port, vertical, probe), the acquisition, its timebase, trigger and ADC group,
  // the measurements and the waveform transfer (the last five joined by dependencies, not signal wires).
  await expect(graph.getByTestId("internal-component")).toHaveCount(18);
  for (const name of ["timebase", "trigger", "adc", "meas"])
    await expect(graph.locator(`[data-name="${name}"]`)).toBeVisible();
  await expect(graph.locator('[data-name="xfer"]')).toBeVisible();
  await expect(graph.getByTestId("internal-group")).toHaveCount(0);
  // What the acquisition uses and who uses it (#729).
  await graph.locator('[data-name="acq"]').click();
  const refs = page.getByTestId("internal-refs");
  await expect(refs).toContainText("使用");
  for (const name of ["timebase", "trigger", "adc", "meas", "xfer"])
    await expect(refs).toContainText(name);
  // What controls it and what it affects (#739): this scope's ADC group follows no setting, but
  // the waveform readings depend on it, by the route they take.
  await graph.locator('[data-name="adc"]').click();
  const controls = page.getByTestId("internal-controls");
  await expect(controls).toContainText("由什么控制");
  await expect(controls).toContainText("没有设置或命令直接驱动它");
  const readings = page.getByTestId("internal-readings");
  await expect(readings).toContainText(":WAVeform:DATA?");
  await expect(readings).toContainText("采集");
  // The graph lights its chain: the acquisition it feeds and the transfer after it; a channel is not on it.
  await expect(graph.locator('[data-name="acq"]')).toHaveAttribute("data-lit", "");
  await expect(graph.locator('[data-name="xfer"]')).toHaveAttribute("data-lit", "");
  await expect(graph.locator('[data-name="ch2.front"]')).not.toHaveAttribute(
    "data-lit",
  );
  // A component on the route selects it: the transfer, whose format setting and command drive it.
  await readings.getByRole("button", { name: "波形传输" }).first().click();
  await expect(details).toContainText("waveform_transfer");
  await expect(controls).toContainText(":WAVeform:FORMat");
  // Finding a command: the component it lands on.
  await graph.locator(".react-flow__pane").click({ position: { x: 5, y: 5 } });
  await page.getByTestId("internals-find").fill(":WAV:DATA?");
  const found = page.getByTestId("internals-find-results");
  await expect(found).toContainText(":WAVeform:DATA?");
  await found.getByRole("button").first().click();
  await expect(details).toContainText("waveform_transfer");
  // Read-only: nothing to drag or connect.
  await expect(graph.locator(".react-flow__node.draggable")).toHaveCount(0);
});

test("instrument internals: what only commands drive waits under its own label (#729)", async ({
  page,
}) => {
  const start = await page.request.post("/api/sessions", {
    headers: { "x-simubench-token": TOKEN },
    data: { bench_path: "benches/keysight-dsox2024a.bench.yaml", force: true },
  });
  const sid = ((await start.json()) as { id: string }).id;
  await page.goto(`/s/${sid}?token=${TOKEN}`);
  await page
    .locator(".react-flow__node")
    .filter({ hasText: "示波器" })
    .first()
    .click();
  await page.getByRole("button", { name: "查看内部结构" }).click();
  const graph = page.getByTestId("internals-graph");
  await expect(graph.getByTestId("internal-command-side")).toBeVisible();
  // The system block and the status registers are not on the signal flow; the acquisition's stages are.
  await graph.locator('[data-name="sys"]').click();
  await expect(page.getByTestId("internal-refs")).toContainText("不在信号流上");
  // Commands are its reason to exist (#739): it lists the ones that operate it.
  await expect(page.getByTestId("internal-controls")).toContainText("只由命令操作");
  await expect(page.getByTestId("internal-controls")).toContainText(":SYSTem:PRESet");
  await graph.locator('[data-name="adc"]').click();
  await expect(page.getByTestId("internal-refs")).toContainText("acq");
  await expect(page.getByTestId("internal-refs")).not.toContainText(
    "不在信号流上",
  );
});

test("internals: a setting row locates its field in the inspector, in the right channel and group (#800)", async ({
  page,
}) => {
  const start = await page.request.post("/api/sessions", {
    headers: { "x-simubench-token": TOKEN },
    data: { bench_path: "benches/keysight-dsox2024a.bench.yaml", force: true },
  });
  const sid = ((await start.json()) as { id: string }).id;
  await page.goto(`/s/${sid}?token=${TOKEN}`);
  await page.locator(".react-flow__node").filter({ hasText: "示波器" }).first().click();
  await page.getByRole("button", { name: "查看内部结构" }).click();
  const graph = page.getByTestId("internals-graph");

  // A whole-instrument setting: the inspector opens its group and focuses the field.
  await graph.locator('[data-name="timebase"]').click();
  await page.getByTestId("internal-locate-timebase.scale").click();
  const scale = page.locator('[data-setting="timebase.scale"]');
  await expect(scale).toBeVisible();
  await expect(scale.locator("input")).toBeFocused();

  // A channel's setting: the inspector switches to that channel first (CH1 is the one it shows).
  await expect(page.getByTestId("channel-settings").getByRole("tab", { selected: true })).toContainText("1");
  await page.getByTestId("internals-find").fill("ch3.scale");
  await page.getByTestId("internals-find-results").getByRole("button").first().click();
  await page.getByTestId("internal-locate-ch3.scale").click();
  await expect(page.getByTestId("channel-settings").getByRole("tab", { selected: true })).toContainText("3");
  await expect(page.locator('[data-setting="ch3.scale"] input')).toBeFocused();
});

test("the settings groups span the screen, not a nested grid's first column", async ({
  page,
}) => {
  // issue 693: a caller wrapping SettingsGroups in its own grid section squeezed the groups
  // into one column — the container must be as wide as its parent on each of these screens.
  for (const [bench, node] of [
    ["smu", "源测量单元"],
    ["lockin_rc", "锁相放大器"],
    ["rfgen", "射频信号源"],
  ] as const) {
    await page.goto(`/?token=${TOKEN}`);
    await page
      .getByRole("listitem")
      .filter({ has: page.getByText(bench, { exact: true }) })
      .getByRole("button", { name: /^(打开测试台|进入)$/ })
      .click();
    await page
      .locator(".react-flow__node")
      .filter({ hasText: node })
      .dblclick();
    const groups = page.getByTestId("settings-groups");
    await expect(groups).toBeVisible();
    const box = (await groups.boundingBox())!;
    const parent = (await groups.locator("xpath=..").boundingBox())!;
    expect(
      box.width,
      `${bench}: the settings groups are as wide as their parent`,
    ).toBeGreaterThan(parent.width * 0.95);
  }
});

test("number fields step up and down: arrow keys, the arrow buttons and the wheel (#738)", async ({
  page,
}) => {
  const start = await page.request.post("/api/sessions", {
    headers: { "x-simubench-token": TOKEN },
    data: { bench_path: "benches/keysight-dsox2024a.bench.yaml", force: true },
  });
  const sid = ((await start.json()) as { id: string }).id;
  await page.goto(`/s/${sid}?token=${TOKEN}`);
  await page
    .locator(".react-flow__node")
    .filter({ hasText: "示波器" })
    .first()
    .click();
  const timebase = page.getByRole("textbox", { name: "时基", exact: true });
  const scpi = async (command: string) =>
    (
      (await (
        await page.request.post(`/api/sessions/${sid}/instruments/scope/scpi`, {
          headers: { "x-simubench-token": TOKEN },
          data: { command },
        })
      ).json()) as { response: string }
    ).response.trim();
  await expect(timebase).toHaveValue("200 µs");
  // ↑ steps a tenth of the decade (1.5 kHz would move 100 Hz); the instrument takes it at once.
  await timebase.focus();
  await timebase.press("ArrowUp");
  await expect(timebase).toHaveValue("210 µs");
  expect(Number(await scpi(":TIMebase:SCALe?"))).toBeCloseTo(2.1e-4, 9);
  // Shift: ten times as far.
  await timebase.press("Shift+ArrowDown");
  await expect(timebase).toHaveValue("110 µs");
  // The arrow buttons step too, and keep the field's focus so the wheel then steps the same field.
  await page.getByRole("button", { name: "增大 时基" }).click();
  await expect(timebase).toHaveValue("120 µs");
  await timebase.hover();
  await page.mouse.wheel(0, -120);
  await expect(timebase).toHaveValue("130 µs");
  await page.mouse.wheel(0, 120);
  await expect(timebase).toHaveValue("120 µs");
  // At the end of the range the arrow is greyed out: the time base stops at 2 ns.
  await timebase.fill("2.1 ns");
  await timebase.press("Enter");
  await expect(timebase).toHaveValue("2.1 ns");
  await timebase.focus();
  await timebase.press("Shift+ArrowDown");
  await expect(timebase).toHaveValue("2 ns");
  await expect(page.getByRole("button", { name: "减小 时基" })).toBeDisabled();
});

test("the CH selector shows the picked channel's settings and always a valid channel (#733)", async ({
  page,
}) => {
  const start = await page.request.post("/api/sessions", {
    headers: { "x-simubench-token": TOKEN },
    data: { bench_path: "benches/keysight-dsox2024a.bench.yaml", force: true },
  });
  const sid = ((await start.json()) as { id: string }).id;
  await page.goto(`/s/${sid}?token=${TOKEN}`);
  await page
    .locator(".react-flow__node")
    .filter({ hasText: "示波器" })
    .first()
    .click();
  const tabs = page.getByTestId("channel-settings").getByRole("tab");
  await expect(tabs).toHaveCount(4);
  // CH1 first, and its settings are CH1's: the bench sets ch1.scale to 0.5 V/div.
  await expect(tabs.first()).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("textbox", { name: "垂直档位" })).toBeVisible();
  // Configure CH3 to a different scale; clicking CH3 shows CH3's own value.
  await page.request.post(`/api/sessions/${sid}/instruments/scope/configure`, {
    headers: { "x-simubench-token": TOKEN },
    data: { path: "ch3.scale", value: 0.2 },
  });
  await tabs.nth(2).click();
  await expect(tabs.nth(2)).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("textbox", { name: "垂直档位" })).toHaveValue(
    /200 m/,
  );
  // Selecting the two-channel generator and coming back: the picker is valid on both — the
  // generator's own CH1/CH2, and the scope back on CH1 with its own 500 mV.
  await page
    .locator(".react-flow__node")
    .filter({ hasText: "函数发生器" })
    .first()
    .click();
  await expect(page.getByRole("textbox", { name: "垂直档位" })).toHaveCount(0); // the scope's pane is gone
  await expect(
    page.getByTestId("channel-settings").getByRole("tab"),
  ).toHaveCount(2);
  await expect(
    page.getByTestId("channel-settings").getByRole("tab").first(),
  ).toHaveAttribute("aria-selected", "true");
  await page
    .locator(".react-flow__node")
    .filter({ hasText: "示波器" })
    .first()
    .click();
  // Back on the scope the picked CH3 is valid again and its settings are the ones shown — the
  // picker never sits on a channel the instrument does not have (#733).
  await expect(tabs.nth(2)).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("textbox", { name: "垂直档位" })).toHaveValue(
    /200 m/,
  );
});

test("the fault panel queues an error code the given number of times (#697, #766)", async ({
  page,
}) => {
  await startBench(page);
  const sid = page.url().match(/\/s\/([0-9a-f]+)/)![1];
  await page
    .locator(".react-flow__node")
    .filter({ hasText: "函数发生器" })
    .click();
  const faults = page
    .locator("section")
    .filter({ hasText: "故障注入" })
    .first();
  await faults.getByRole("button").first().click(); // open the folded section
  await faults.getByLabel("故障类型").click();
  await page.getByRole("option", { name: /错误码/ }).click();
  const code = faults.getByPlaceholder("-300");
  await expect(code).toBeVisible();
  // #697: error gets a count field, and the queue answers the code that many times
  const count = faults.getByPlaceholder("留空表示不限");
  await expect(count).toBeVisible();
  await code.fill("-313");
  await count.fill("3");
  await faults.getByRole("button", { name: "注入", exact: true }).click();
  await expect(faults.getByText(/错误码/).first()).toBeVisible(); // the fault lists as in effect
  const scpi = async () =>
    (
      await page.request.post(`/api/sessions/${sid}/instruments/awg/scpi`, {
        headers: { "x-simubench-token": TOKEN },
        data: { command: ":SYSTem:ERRor?" },
      })
    )
      .json()
      .then((r) => (r as { response: string }).response.trim());
  expect(await scpi()).toContain("-313");
  expect(await scpi()).toContain("-313");
  expect(await scpi()).toContain("-313");
  expect((await scpi()).startsWith("0,")).toBe(true); // used up: the queue is empty again
});
