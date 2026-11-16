import { expect, type Page, test } from "@playwright/test";

import { benchRow, expectContrastAA, expectText, expectValue, isClockTime, isSession, isVersion, near, openButton, quantity, sessionIdOf } from "./judge";

const TOKEN = "e2e-token";

async function startBench(page: Page) {
  await page.goto(`/?token=${TOKEN}`);
  await expect(page.getByRole("heading", { name: "我的测试台", level: 2 })).toBeVisible();
  await openButton(page.getByRole("listitem").filter({ hasText: "awg_rc_scope" }))
    .click();
  await expect(page).toHaveURL(isSession);
  await expect(page.locator(".react-flow__node")).toHaveCount(3);
}

test("the token is removed from the address bar", async ({ page }) => {
  await page.goto(`/?token=${TOKEN}`);
  await expect(page.getByRole("heading", { name: "我的测试台", level: 2 })).toBeVisible();
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
  await expect(page.getByLabel("-222")).toBeVisible();
});

test("SCPI console, communication log and problems", async ({ page }) => {
  await startBench(page);
  await page.getByRole("button", { name: "示波器" }).first().click();
  const input = page.getByRole("textbox", { name: "发送 SCPI 命令" });
  await input.fill("*IDN?");
  await input.press("Enter");
  await expect(page.getByText("SIMUBENCH,SB-DSO-4").first()).toBeVisible();
  await input.fill(":CHANnel9:SCALe 1");
  await input.press("Enter");
  await expect(page.getByRole("tab", { name: "问题" })).toContainText("1");
  await page.getByRole("tab", { name: "问题" }).click();
  await expect(
    page.getByRole("button", { name: "-114" }).and(page.getByRole("button", { name: "Header suffix out of range" })),
  ).toBeVisible();
  await page.getByRole("tab", { name: "通信" }).click();
  const row = page.getByText(":CHANnel9:SCALe 1").locator("..");
  await expect(row).toBeVisible();
  await expect(row).toContainText("界面"); // who sent it
  await expectText(row.getByTestId("wall-time"), isClockTime, "HH:MM:SS.mmm"); // real time beside virtual time
  // The source filter narrows the log to scripts, agents, the UI or replays.
  const source = page.getByRole("combobox", { name: "按来源过滤" });
  await source.click();
  await page.getByRole("option", { name: "脚本" }).click();
  await expect(page.getByText(":CHANnel9:SCALe 1")).toHaveCount(0);
  await source.click();
  await page.getByRole("option", { name: "界面" }).click();
  await expect(page.getByText(":CHANnel9:SCALe 1")).toBeVisible();
});

test("open the scope screen by double-clicking its node", async ({ page }) => {
  await startBench(page);
  await page
    .locator(".react-flow__node")
    .filter({ hasText: "示波器" })
    .dblclick();
  await expect(page.getByRole("tab", { name: "示波器" })).toBeVisible();
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
  await expect(page.getByTestId("scope-source")).toHaveText("采集次数 1");
  await controls.getByRole("button", { name: "运行" }).click();
  await expect(page.getByText("运行中", { exact: true })).toBeVisible();
  await page.waitForTimeout(1000); // the screen redraws while running: frames, not acquisitions
  await expect(page.getByTestId("scope-source")).toHaveText("实时画面"); // said so, not a count (#280)
  await expect(page.getByText(":SINGle")).toHaveCount(0); // nothing in the communication log
  await page.getByRole("tab", { name: "事件" }).click();
  await expect(page.getByText("scope 面板: 单次")).toBeVisible();
  await expect(page.getByText("scope 面板: 运行")).toBeVisible();
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
  await openButton(page.getByRole("listitem").filter({ hasText: "siglent_scope_rc" }))
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
  await expect(page.getByTestId("scope-source")).toHaveText("采集次数 2");
  await expect(page.getByText(":TRIGger:MODE SINGle")).toHaveCount(0); // run inside the scope, not over SCPI
  await page.getByRole("tab", { name: "问题" }).click();
  await expect(page.getByText("-113")).toHaveCount(0); // the bench's probe warning is the only problem
});

test("open screens are remembered for the bench and reopen on the next start", async ({
  page,
}) => {
  await startBench(page);
  await page
    .locator(".react-flow__node")
    .filter({ hasText: "示波器" })
    .dblclick();
  await expect(page.getByRole("tab", { name: "示波器" })).toBeVisible();
  await page.getByRole("button", { name: "返回测试台列表" }).click();
  // Start again: the scope screen comes back as the active tab, not the canvas.
  await openButton(page.getByRole("listitem").filter({ hasText: "awg_rc_scope" }))
    .click();
  await expect(page).toHaveURL(isSession);
  await expect(page.getByRole("tab", { name: "示波器" })).toBeVisible();
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
  await expect(page.locator("html")).toContainClass("dark");
});

test("run record: open it from the home page, see the timeline, replay identically", async ({
  page,
}) => {
  await page.goto(`/?token=${TOKEN}`);
  await openButton(page.getByRole("listitem").filter({ hasText: "awg_rc_scope" }))
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
  const file = info.record_path.slice(Math.max(info.record_path.lastIndexOf("/"), info.record_path.lastIndexOf("\\")) + 1);
  expect(file.startsWith("run-") && file.endsWith(".jsonl"), file).toBe(true);
  const runId = file.slice("run-".length, file.length - ".jsonl".length);
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
  await openButton(page.getByRole("listitem").filter({ hasText: "awg_rc_scope" }))
    .click();
  await expect(page.locator(".react-flow__node")).toHaveCount(3);
  await page.getByRole("button", { name: "net-1" }).click();
  const probe = page.getByTestId("probe");
  await expect(probe).toContainText("没有信号源连到这个网络"); // the generator's output is off
  const input = page.getByRole("textbox", { name: "发送 SCPI 命令" });
  await page
    .locator(".react-flow__node")
    .filter({ hasText: "函数发生器" })
    .click();
  await input.fill(":OUTPut1:STATe ON");
  await input.press("Enter");
  await page.getByRole("button", { name: "net-1" }).click();
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
  await expectText(page.getByTestId("about-version"), isVersion, "a version x.y.z");
  await expect(page.getByTestId("about-doctor")).toContainText(
    "pyvisa backend",
  );
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出诊断包" }).click();
  const name = (await download).suggestedFilename();
  expect(name.startsWith("simubench-diagnostics-") && name.endsWith(".zip"), name).toBe(true);
});

test("home: the product page shows the same bench in the web, the terminal and a script", async ({ page }) => {
  await page.goto(`/?token=${TOKEN}`);
  await expect(page.getByRole("heading", { level: 1 })).toContainText("搭一个虚拟测试台");
  await expect(page.getByLabel("Hello World 测试台的实时预览")).toContainText("引擎实测数据");
  const show = page.getByTestId("showcase");
  await show.getByRole("tab", { name: "终端控制台" }).click();
  await expect(show.getByRole("tabpanel").getByRole("img", { name: "总览：每台仪表一张状态卡片" })).toBeVisible();
  await show.getByRole("tab", { name: "脚本与 Agent" }).click();
  // Reduced motion (playwright.config.ts): the session is shown whole, not typed out.
  await expect(show.getByRole("tabpanel")).toContainText("@simubench");
  await expect(show.getByRole("tabpanel")).toContainText("N9320B");
  await expect(page.getByTestId("instrument-list").getByRole("button", { name: "示波器" })).toBeVisible();
});

test("instrument internals: the scope's components, wiring and panel ports, read-only (issue 20)", async ({
  page,
}) => {
  await page.goto(`/?token=${TOKEN}`);
  await openButton(page.getByRole("listitem").filter({ hasText: "awg_rc_scope" }))
    .click();
  await page.locator(".react-flow__node").filter({ hasText: "示波器" }).click();
  await page.getByRole("button", { name: "查看内部结构" }).click();
  await expect(
    page.getByRole("tab", { name: "示波器 · 内部结构" }),
  ).toBeVisible();
  const graph = page.getByTestId("internals-graph");
  // CH1 is open: input port -> vertical channel -> probe setting -> acquisition; the other channels
  // are collapsed groups (instrument-authoring.md 8.4).
  for (const name of ["ch1.inp", "ch1.front", "ch1.probe", "acq"])
    await expect(graph.locator(`[data-name="${name}"]`)).toBeVisible();
  await expect(graph.getByTestId("internal-group")).toHaveCount(3);
  await expect(graph.getByTestId("internal-port")).toHaveCount(4);
  // wires: CH1's chain to the ADC (3) and its panel port, the groups to the ADC (3) and their ports
  // (3), then the sample stream (D67): ADC -> decimator -> acquisition, one wire per channel (4 + 4);
  // dependencies (#729): the time base, trigger, ADC group and decimator feed the acquisition, the
  // time base and ADC group feed the decimator, and the acquisition feeds the measurements and the
  // transfer (8); math (#1074) sits between them: the acquisition feeds it, it feeds the
  // measurements and the transfer, whose function sources read through it (3).
  await expect(graph.locator(".react-flow__edge")).toHaveCount(4 + 3 + 3 + 8 + 8 + 3);
  await expect(
    graph.locator('.react-flow__edge-path[style*="stroke-dasharray"]'),
  ).toHaveCount(11);
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
  // 4 channels × (input port, vertical, probe), the ADC group, the decimator, the acquisition, its
  // timebase and trigger, the measurements, the waveform transfer and the math block (#1074).
  await expect(graph.getByTestId("internal-component")).toHaveCount(20);
  for (const name of ["timebase", "trigger", "adc", "dec", "meas"])
    await expect(graph.locator(`[data-name="${name}"]`)).toBeVisible();
  await expect(graph.locator('[data-name="xfer"]')).toBeVisible();
  await expect(graph.getByTestId("internal-group")).toHaveCount(0);
  // Four open channels do not fit at a readable size: bring the whole graph in before clicking across it.
  await graph.locator(".react-flow__controls-fitview").click();
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
  // The graph lights its chain: the channels that feed it (the signal runs through the ADC, D67),
  // the acquisition it feeds and the transfer after it; the trigger is not on it.
  await expect(graph.locator('[data-name="acq"]')).toHaveAttribute("data-lit", "");
  await expect(graph.locator('[data-name="xfer"]')).toHaveAttribute("data-lit", "");
  await expect(graph.locator('[data-name="ch2.front"]')).toHaveAttribute("data-lit", "");
  await expect(graph.locator('[data-name="trigger"]')).not.toHaveAttribute(
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
  // The view opens at a readable size from the signal's start, so part of the graph is off screen
  // (the overview map says where): the "fit view" control brings all of it in, and the map goes.
  await expect(graph.locator(".react-flow__minimap")).toBeVisible();
  await graph.locator(".react-flow__controls-fitview").click();
  await expect(graph.locator(".react-flow__minimap")).toHaveCount(0);
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

test("internal probe: the ADC's samples and resolution, then peak detect at the decimator (signal-chain.md 1.5 step 5, #953)", async ({
  page,
}) => {
  const headers = { "x-simubench-token": TOKEN };
  // The example bench on a clock idle real time does not move (fixtures/benches/dso-probe):
  // normal and peak detect are compared at one instant, not wherever the machine's speed puts
  // them — at a few instants (1.84 s) the two frames happen to span the same.
  const start = await page.request.post("/api/sessions", {
    headers,
    data: { bench_path: "benches/dso-probe.bench.yaml", force: true },
  });
  const sid = ((await start.json()) as { id: string }).id;
  const scpi = (name: string, command: string) =>
    page.request.post(`/api/sessions/${sid}/instruments/${name}/scpi`, { headers, data: { command } });
  // The generator's output is off at power-on (as on the real one): turn it on, so CH1 carries the RC's output.
  expect((await scpi("awg", "C1:OUTPut ON")).ok()).toBe(true);
  expect((await page.request.post(`/api/sessions/${sid}/clock/advance`, { headers, data: { seconds: 0.026 } })).ok()).toBe(true);
  await page.goto(`/s/${sid}?token=${TOKEN}`);
  await page.locator(".react-flow__node").filter({ hasText: "示波器" }).first().click();
  await page.getByRole("button", { name: "查看内部结构" }).click();
  const graph = page.getByTestId("internals-graph");

  // The page says what the diagram is, and what it is drawn from (the definition's reference).
  await expect(page.getByTestId("internals-basis")).toContainText("功能框图级的模型");
  await expect(page.getByTestId("internals-basis")).toContainText("2000 X-Series");
  // Wires say what they carry, in words: the ADC's stream is digital, at its rate and resolution.
  await expect(graph.locator(".react-flow__edge-text", { hasText: "数字 · 2 GSa/s · 8 位" })).toHaveCount(1);
  await expect(graph.locator(".react-flow__edge-text", { hasText: "模拟" }).first()).toBeVisible();

  // Every output on screen carries the signal there, without a click: CH1's analog chain as a line,
  // the ADC's stream as steps, the unconnected EXT input as a dotted rule (it reads 0).
  const mini = (port: string) => graph.locator(`[data-port="${port}"]`).getByTestId("internal-mini");
  await expect(mini("ch1.inp.out")).toHaveAttribute("data-kind", "analog");
  await expect(mini("adc.out1")).toHaveAttribute("data-kind", "samples");
  await expect(mini("extin.out")).toHaveAttribute("data-kind", "silent");

  // The ADC's output, opened from the keyboard: the samples, its rate and resolution.
  const adcOut = graph.locator('[data-port="adc.out1"]');
  await adcOut.focus();
  await page.keyboard.press("Enter");
  const probe = page.getByTestId("internal-probe");
  await expect(probe).toHaveAttribute("data-state", "ready");
  await expect(page.getByTestId("internal-probe-heading")).toHaveText("ADC 组 · out1");
  await expect(page.getByTestId("internal-probe-tag")).toHaveText("数字 · 2 GSa/s · 8 位");
  await expect(page.getByTestId("internal-probe-format")).toHaveText("8 位");
  await expect(probe).toContainText("4096 个采样");
  await expect(page.getByTestId("internal-probe-fullscale")).toContainText("用到满量程的");
  await expect(page.getByTestId("internal-probe-wave")).toBeVisible();
  // The frame is longer than the picture is wide: its first samples are shown close up, on the converter's codes.
  await expect(page.getByTestId("internal-probe-closeup")).toBeVisible();
  await expect(page.getByTestId("internal-probe-lsb")).toContainText("1 LSB = ");
  await expect(page.getByTestId("internal-probe-spectrum")).toBeVisible();
  await expect(adcOut).toHaveAttribute("aria-pressed", "true");

  // The decimator's output in normal mode, then with peak detect: it keeps each interval's
  // extremes at the ADC's rate, so the frame spans more.
  await graph.locator('[data-port="dec.out1"]').click();
  await expect(page.getByTestId("internal-probe-heading")).toHaveText("抽取与采集方式 · out1");
  await expect(page.getByTestId("internal-probe-tag")).toContainText("数字 · 50 MSa/s · 8 位");
  const span = async () => {
    const lo = quantity((await page.getByTestId("internal-probe-lo").textContent()) ?? "", "V");
    const hi = quantity((await page.getByTestId("internal-probe-hi").textContent()) ?? "", "V");
    expect(lo).not.toBeNull();
    expect(hi).not.toBeNull();
    return hi! - lo!;
  };
  const normal = await span();
  expect(normal).toBeGreaterThan(0);
  expect((await scpi("scope", ":ACQuire:TYPE PEAK")).ok()).toBe(true);
  // The probe follows the event version (D64): the new mode shows without a click.
  await expect.poll(span).toBeGreaterThan(normal);
  await expect(page.getByTestId("internal-probe-heading")).toHaveText("抽取与采集方式 · out1");

  // Closing the probe leaves the component's details.
  await probe.getByRole("button", { name: "关闭内部探针" }).click();
  await expect(page.getByTestId("internal-probe")).toHaveCount(0);
  await expect(page.getByTestId("internal-details")).toContainText("scope_decimator");
});

test("internal probe: a narrow pulse the decimator drops in normal acquisition and keeps with peak detect, in AA contrast (signal-chain.md 1.5 step 5, #984)", async ({
  page,
}) => {
  const headers = { "x-simubench-token": TOKEN };
  // 20 ns pulses from 0 to 2 V every 10 µs straight into CH1 (fixtures/benches/dso-pulse); at
  // 1 ms/div the decimator keeps one sample per 100 ns, at the same phase of every pulse.
  const start = await page.request.post("/api/sessions", {
    headers,
    data: { bench_path: "benches/dso-pulse.bench.yaml", force: true },
  });
  const sid = ((await start.json()) as { id: string }).id;
  const scpi = (name: string, command: string) =>
    page.request.post(`/api/sessions/${sid}/instruments/${name}/scpi`, { headers, data: { command } });
  expect((await scpi("awg", "C1:OUTPut ON")).ok()).toBe(true);
  expect((await page.request.post(`/api/sessions/${sid}/clock/advance`, { headers, data: { seconds: 0.026 } })).ok()).toBe(true);
  await page.goto(`/s/${sid}?token=${TOKEN}`);
  await page.locator(".react-flow__node").filter({ hasText: "示波器" }).first().click();
  await page.getByRole("button", { name: "查看内部结构" }).click();
  const graph = page.getByTestId("internals-graph");
  const volts = async (id: string) => {
    const v = quantity((await page.getByTestId(id).textContent()) ?? "", "V");
    expect(v).not.toBeNull();
    return v!;
  };

  // The ADC sees every pulse: its stream reaches the pulse's top.
  // (opened from the keyboard: the view opens at a readable zoom, not every port on screen)
  await graph.locator('[data-port="adc.out1"]').focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("internal-probe")).toHaveAttribute("data-state", "ready");
  await expect(page.getByTestId("internal-probe-heading")).toHaveText("ADC 组 · out1");
  expect(await volts("internal-probe-hi")).toBeGreaterThan(1.5);

  // Normal acquisition: one sample per 100 ns, none of them on a 20 ns pulse — the record
  // never rises above 1 V, the pulses are gone.
  await graph.locator('[data-port="dec.out1"]').click();
  await expect(page.getByTestId("internal-probe-heading")).toHaveText("抽取与采集方式 · out1");
  await expect(page.getByTestId("internal-probe-tag")).toContainText("数字 · 10 MSa/s · 8 位");
  expect(await volts("internal-probe-hi")).toBeLessThan(1.0);

  // The probe panel and the wire labels read in AA contrast, in both themes.
  const texts = page
    .getByTestId("internal-probe")
    .locator('[data-testid="internal-probe-heading"], [data-testid="internal-probe-tag"], [data-testid="internal-probe-lo"], [data-testid="internal-probe-hi"]');
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    await expect
      .poll(() => page.evaluate(() => document.documentElement.classList.contains("dark")))
      .toBe(scheme === "dark");
    await expectContrastAA(texts);
    await expectContrastAA(graph.locator(".react-flow__edge-text"));
  }
  await page.emulateMedia({ colorScheme: "light" });

  // Peak detect keeps each interval's extremes: the pulses are back at their full height.
  expect((await scpi("scope", ":ACQuire:TYPE PEAK")).ok()).toBe(true);
  await expect.poll(() => volts("internal-probe-hi")).toBeGreaterThan(1.5);
  await expect(page.getByTestId("internal-probe-heading")).toHaveText("抽取与采集方式 · out1");
});

test("internal probe on the spectrum analyzer: the chain, the mixer's products, tuned to, the ADC and its resolution (signal-chain.md 1.5 steps 1-4, #959)", async ({
  page,
}) => {
  const headers = { "x-simubench-token": TOKEN };
  const BENCH = "benches/sa-two-tone.bench.yaml";
  // Two generators at 1000 and 1001 MHz on the digital-IF analyzer (fixtures/benches/sa-two-tone),
  // on a clock idle real time does not move: every probe below reads one instant.
  const open = async () => {
    const start = await page.request.post("/api/sessions", { headers, data: { bench_path: BENCH, force: true } });
    expect(start.ok()).toBe(true);
    const sid = ((await start.json()) as { id: string }).id;
    await page.goto(`/s/${sid}?token=${TOKEN}`);
    await page.locator(".react-flow__node").filter({ hasText: "频谱仪" }).first().click();
    await page.getByRole("button", { name: "查看内部结构" }).click();
    await expect(page.getByTestId("internals-graph").getByTestId("internal-component").first()).toBeVisible();
    return sid;
  };
  let sid = await open();
  const graph = page.getByTestId("internals-graph");
  const probe = page.getByTestId("internal-probe");
  const heading = page.getByTestId("internal-probe-heading");
  const tune = page.getByTestId("internal-probe-tune");
  const lineTable = page.getByTestId("internal-probe-line-table");
  const block = (name: string) => graph.locator(`[data-testid="internal-component"][data-name="${name}"]`);
  const box = async (name: string) => (await block(name).boundingBox())!;

  // Step 1 — the diagram follows the signal, left to right, in one line: RF input → attenuator →
  // mixer → IF filter → ADC → down-converter → RBW filter / FFT → detector → video filter → trace.
  await graph.locator(".react-flow__controls-fitview").click();
  const chain: [string, string][] = [
    ["rf_in", "输入端口"],
    ["input", "输入衰减器"],
    ["mixer", "混频器"],
    ["if_filter", "中频滤波"],
    ["adc", "ADC"],
    ["ddc", "数字下变频"],
    ["rbw", "RBW 滤波 / FFT"],
    ["detector", "检波"],
    ["vbw", "视频滤波"],
    ["sa", "迹线"],
  ];
  for (const [name, label] of chain) await expect(block(name)).toContainText(label);
  await expect
    .poll(async () => {
      const boxes = await Promise.all(chain.map(([name]) => box(name)));
      const inOrder = boxes.every((b, i) => i === 0 || b.x > boxes[i - 1].x + boxes[i - 1].width);
      const oneLine = boxes.every((b) => Math.abs(b.y + b.height / 2 - (boxes[0].y + boxes[0].height / 2)) < 2);
      return inOrder && oneLine;
    })
    .toBe(true);
  // The oscillator stands under the mixer, its reference to the left of it, both under the line.
  await expect(block("lo")).toContainText("本振");
  await expect(block("clock")).toContainText("参考时钟");
  const [mixer, lo, clock, attenuator] = await Promise.all(["mixer", "lo", "clock", "input"].map(box));
  expect(Math.abs(lo.x - mixer.x)).toBeLessThan(2);
  expect(lo.y).toBeGreaterThan(mixer.y + mixer.height);
  expect(clock.x + clock.width).toBeLessThan(lo.x);
  expect(clock.y).toBeGreaterThan(attenuator.y + attenuator.height);
  // The wires say what they carry: analog up to the converter, then sample streams.
  await expect(graph.locator(".react-flow__edge-text", { hasText: "数字 · 100 MSa/s · 16 位" })).toHaveCount(1);
  await expect(graph.locator(".react-flow__edge-text", { hasText: "IQ" })).toHaveCount(1);

  // Step 2 — the mixer's IF output, read at the center frequency (1000.5 MHz): the two tones on
  // the 25 MHz IF, 1 MHz apart, and their third-order products 1 MHz outside them, 60 dB down.
  await graph.locator('[data-port="mixer.out"]').click();
  await expect(probe).toHaveAttribute("data-state", "ready");
  await expect(heading).toHaveText("混频器 · 中频输出");
  await expect(page.getByTestId("internal-probe-tag")).toHaveText("模拟");
  await expect(tune).toContainText("调谐于");
  await expect(tune).toHaveAttribute("data-tune", "center");
  await expect(tune).toHaveAttribute("data-hz", "1000500000");
  await expect(page.getByTestId("internal-probe-tune-center")).toHaveAttribute("aria-checked", "true");
  for (const f of ["24.5 MHz", "25.5 MHz", "23.5 MHz", "26.5 MHz"]) await expect(lineTable).toContainText(f);
  const product = lineTable.getByRole("row").filter({ hasText: "23.5 MHz" });
  const dbc = quantity(((await product.getByRole("cell").last().textContent()) ?? "").trim());
  expect(near(dbc, -60, 1)).toBe(true); // −20 dBm at the mixer, a +10 dBm intercept
  // The picture is the IF group, not 0 Hz to the 2 GHz sum products: the four lines stand apart.
  await expect(page.getByTestId("internal-probe-lines")).toHaveAttribute("data-zoomed", "");
  await expect(page.getByTestId("internal-probe-lines-outside")).toBeVisible();
  await page.getByTestId("internal-probe-lines-whole").click();
  await expect(page.getByTestId("internal-probe-lines-outside")).toHaveCount(0);
  await page.getByTestId("internal-probe-lines-whole").click();
  // The cursor reads a line: frequency, amplitude, level under the strongest.
  await page.getByTestId("internal-probe-lines").focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByTestId("internal-probe-cursor")).toContainText("dBc");

  // Tuned to the marker (1000 MHz): the tones move 0.5 MHz up the IF — the probe follows the tuning.
  await page.getByTestId("internal-probe-tune-marker-1").click();
  await expect(tune).toHaveAttribute("data-tune", "mark");
  for (const f of ["25 MHz", "26 MHz", "24 MHz", "27 MHz"]) await expect(lineTable).toContainText(f);
  // A typed tuning, with its unit; one the analyzer cannot tune to is refused where it was typed.
  const field = tune.getByRole("textbox", { name: "调谐频率" });
  await field.fill("8 GHz");
  await field.press("Enter");
  await expect(tune.getByRole("alert")).toContainText("超出这台仪表的频率范围");
  await expect(tune).toHaveAttribute("data-tune", "mark");
  await field.fill("1001 MHz");
  await field.press("Enter");
  await expect(tune).toHaveAttribute("data-tune", "manual");
  await expect(tune).toHaveAttribute("data-hz", "1001000000");
  await expect(tune.getByRole("alert")).toHaveCount(0);
  for (const f of ["24 MHz", "25 MHz", "23 MHz", "26 MHz"]) await expect(lineTable).toContainText(f);
  // Before the mixer nothing depends on the tuning: the attenuator's output has no "tuned to".
  await graph.locator('[data-port="input.out"]').click();
  await expect(heading).toHaveText("输入衰减器 · 输出");
  await expect(tune).toHaveCount(0);
  await expect(lineTable).toContainText("1.001 GHz");

  // The tuning stays as the probe moves down the chain. The down-converter's IQ stream: I and Q,
  // and its two-sided spectrum on radio frequencies — the tuned tone (1001 MHz) in the middle.
  await graph.locator('[data-port="ddc.out"]').click();
  await expect(heading).toHaveText("数字下变频 · IQ 输出");
  await expect(tune).toHaveAttribute("data-hz", "1001000000");
  await expect(page.getByTestId("internal-probe-format")).toHaveText("IQ");
  await expect(page.getByTestId("internal-probe-iq").locator("path[data-trace]")).toHaveCount(2);
  const spectrum = page.getByTestId("internal-probe-spectrum");
  await expect(spectrum).toHaveAttribute("data-axis", "radio");
  await expect(page.getByTestId("internal-probe-tuned-at")).toBeVisible();
  // A click on the spectrum tunes there: the other tone, a quarter of the picture to the left.
  await expect(page.getByTestId("internal-probe-peaks")).toContainText("1 GHz");
  await page.getByRole("button", { name: "调谐到 1 GHz" }).click();
  await expect.poll(async () => near(Number(await tune.getAttribute("data-hz")), 1.0e9, 2e3)).toBe(true);
  await expect(tune).toHaveAttribute("data-tune", "manual");
  const picture = (await spectrum.boundingBox())!;
  await spectrum.click({ position: { x: picture.width * 0.9, y: picture.height / 2 } });
  await expect.poll(async () => Number(await tune.getAttribute("data-hz"))).toBeGreaterThan(1.001e9);
  await page.getByTestId("internal-probe-tune-center").click();
  await expect(tune).toHaveAttribute("data-hz", "1000500000");

  // The transform's output: frequency bins, drawn as they are — a spectrum, no waveform.
  await graph.locator('[data-port="rbw.out"]').click();
  await expect(heading).toHaveText("RBW 滤波 / FFT · 频点输出");
  await expect(probe).toContainText("幅度谱（变换输出的频点）");
  await expect(probe).toContainText("不再做变换");
  await expect(spectrum).toHaveAttribute("data-axis", "radio");
  await expect(page.getByTestId("internal-probe-wave")).toHaveCount(0);
  await expect(page.getByTestId("internal-probe-peaks")).toContainText("1.001 GHz");

  // Step 3 — the ADC's output: the title states its rate and resolution; samples and their FFT.
  const adcOut = graph.locator('[data-port="adc.out"]');
  await adcOut.click();
  await expect(heading).toHaveText("ADC · 输出");
  await expect(page.getByTestId("internal-probe-tag")).toHaveText("数字 · 100 MSa/s · 16 位");
  await expect(probe).toContainText("4096 个采样");
  await expect(page.getByTestId("internal-probe-wave")).toBeVisible();
  await expect(spectrum).toHaveAttribute("data-axis", "baseband");
  await expect(tune).toBeVisible(); // after the mixer: still read at a tuning

  // Step 4 — the converter's resolution, a per-unit value of the bench (`hidden`), from 16 to 12
  // bits: the noise floor rises at the ADC's output and, on this analyzer whose trace is made from
  // the samples, on the trace.
  const floorText = page.getByTestId("internal-probe-floor");
  const adcFloor = async () => quantity((await floorText.textContent()) ?? "", "dBFS");
  const traceFloor = async () => {
    const r = await page.request.post(`/api/sessions/${sid}/instruments/sa/scpi`, { headers, data: { command: ":TRACe:DATA?" } });
    expect(r.ok()).toBe(true);
    const levels = ((await r.json()) as { response: string }).response.split(",").map(Number).sort((a, b) => a - b);
    expect(levels.length).toBeGreaterThan(100);
    return levels[levels.length >> 1];
  };
  await expect(floorText).toContainText("dBFS");
  const adc16 = (await adcFloor())!;
  const trace16 = await traceFloor();
  const file = await page.request.get("/api/benches/file", { headers, params: { path: BENCH } });
  const original = (await file.json()) as { sha256: string; data: { instruments: { sa: { hidden: Record<string, unknown> } } } };
  const edited = structuredClone(original.data);
  edited.instruments.sa.hidden.adc = { bits: 12 };
  const saved = await page.request.put("/api/benches/file", { headers, data: { path: BENCH, base_sha256: original.sha256, data: edited } });
  expect(saved.ok()).toBe(true);
  try {
    sid = await open();
    await graph.locator(".react-flow__controls-fitview").click();
    await adcOut.click();
    await expect(page.getByTestId("internal-probe-tag")).toHaveText("数字 · 100 MSa/s · 12 位");
    await expect(floorText).toContainText("dBFS");
    const adc12 = (await adcFloor())!;
    const trace12 = await traceFloor();
    expect(adc12 - adc16).toBeGreaterThan(4);
    expect(trace12 - trace16).toBeGreaterThan(8);
  } finally {
    // The bench as it was: a retry, or another test, starts from 16 bits.
    const now = (await (await page.request.get("/api/benches/file", { headers, params: { path: BENCH } })).json()) as { sha256: string };
    await page.request.put("/api/benches/file", { headers, data: { path: BENCH, base_sha256: now.sha256, data: original.data } });
  }
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

  // The request is spent (#819): leaving the instrument and coming back remounts the inspector,
  // which must not scroll or take the focus again.
  await page.getByRole("tab", { name: "画布" }).click();
  await page.locator(".react-flow__pane").click({ position: { x: 5, y: 5 } });
  await page.locator(".react-flow__node").filter({ hasText: "示波器" }).first().click();
  await expect(page.getByTestId("channel-settings")).toBeVisible();
  await page.waitForTimeout(500);
  await expect(page.locator('[data-setting="timebase.scale"] input')).not.toBeFocused();
  await page.getByRole("tab", { name: "示波器 · 内部结构" }).click();

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
    await openButton(benchRow(page, bench)).click();
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
  await expectValue(page.getByRole("textbox", { name: "垂直档位" }), (v) => near(quantity(v, "V"), 0.2, 1e-9), "200 mV");
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
  await expectValue(page.getByRole("textbox", { name: "垂直档位" }), (v) => near(quantity(v, "V"), 0.2, 1e-9), "200 mV");
});

test("the fault panel queues an error code the given number of times (#697, #766)", async ({
  page,
}) => {
  await startBench(page);
  const sid = sessionIdOf(page.url())!;
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
  await page.getByRole("option", { name: "错误码" }).click();
  const code = faults.getByPlaceholder("-300");
  await expect(code).toBeVisible();
  // #697: error gets a count field, and the queue answers the code that many times
  const count = faults.getByPlaceholder("留空表示不限");
  await expect(count).toBeVisible();
  await code.fill("-313");
  await count.fill("3");
  await faults.getByRole("button", { name: "注入", exact: true }).click();
  await expect(faults.getByText("错误码").first()).toBeVisible(); // the fault lists as in effect
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
