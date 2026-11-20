import { rmSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { WORKSPACE } from "./workspace.mjs";

const TOKEN = "e2e-token";
const headers = { "x-simubench-token": TOKEN };

/** Copies of a model other specs open by name (the 34465A): removed after each test, passed or
 * failed, since a second entry in the instrument list would make their link ambiguous (#1216). */
const transient: string[] = [];
test.afterEach(() => {
  for (const id of transient.splice(0))
    for (const suffix of [".yaml", ".layout.json"]) rmSync(join(WORKSPACE, "instruments", `${id}${suffix}`), { force: true });
});

/** A workspace copy of the 34465A, removed after the test. */
async function copy34465a(request: import("@playwright/test").APIRequestContext): Promise<string> {
  const res = await request.post("/api/definitions/keysight-34465a/copy", { headers });
  const id = ((await res.json()) as { data: { id: string } }).data.id;
  transient.push(id);
  return id;
}

/** The definition file as stored, through the same API the acceptance page reads. */
async function stored(request: import("@playwright/test").APIRequestContext, id: string) {
  const res = await request.get(`/api/definitions/${id}/document`, { headers });
  return (await res.json()) as { data: Record<string, unknown>; ok: boolean };
}

test("build area: drag a component in, wire it, set a parameter, add a panel port, delete (#1201)", async ({ page, request }) => {
  // A workspace copy of a validation instrument: an optical receiver feeding an output stage.
  const copy = await request.post("/api/definitions/sb-oe-1/copy", { headers });
  const id = ((await copy.json()) as { data: { id: string } }).data.id;

  await page.goto(`/i/${id}?token=${TOKEN}`);
  // A model without a declared panel: its settings are grouped as 「整机」, not "INSTRUMENT" (#1251).
  await expect(page.getByText("功能与量程")).toBeVisible();
  await expect(page.getByRole("heading", { name: "整机", level: 4 })).toBeVisible();
  await expect(page.getByRole("heading", { name: "instrument", level: 4 })).toHaveCount(0);
  // Not 「搭建」, the bench's build mode in the top bar: this tab edits the definition's structure (#1222).
  await expect(page.getByTestId("acceptance-tab-build")).toHaveText("编辑结构");
  await page.getByTestId("acceptance-tab-build").click();
  const area = page.getByTestId("build-area");
  await expect(area).toBeVisible();
  // The structure comes from the model it extends, and says so.
  await expect(area.getByText("继承自 sb-oe-1")).toBeVisible();
  await expect(page.getByTestId("build-component")).toHaveCount(2);

  // Drag an output stage from the library onto the canvas, below the per-channel band.
  await page.getByPlaceholder("按类型、名称或说明过滤").fill("output_stage");
  const pane = page.locator(".react-flow__pane");
  const box = (await pane.boundingBox())!;
  await page.getByTestId("build-library-output_stage").dragTo(pane, { targetPosition: { x: box.width / 2, y: box.height - 120 } });
  await expect(page.getByTestId("build-component")).toHaveCount(3);
  await expect(area.getByText("已保存")).toBeVisible();
  let doc = await stored(request, id);
  const names = (doc.data.components as { name: string }[]).map((c) => c.name);
  expect(names).toEqual(["orx", "out", "output_stage"]);

  // Wire the receiver's output to the new stage's input.
  await page
    .locator('[data-name="orx"] .react-flow__handle.source')
    .first()
    .dragTo(page.locator('[data-name="output_stage"] .react-flow__handle.target').first());
  await expect(page.locator(".react-flow__edge")).toHaveCount(2);
  await expect.poll(async () => JSON.stringify((await stored(request, id)).data.wiring)).toContain("orx.out");
  doc = await stored(request, id);
  expect((doc.data.wiring as { instrument: string[][] }).instrument).toContainEqual(["orx.out", "output_stage.in"]);

  // Its output impedance, typed into the side panel.
  await page.locator('[data-name="output_stage"]').click();
  await page.locator("#param-output_stage-z_out").fill("75");
  await page.locator("#param-output_stage-z_out").press("Enter");
  await expect
    .poll(async () => ((await stored(request, id)).data.components as { name: string; params?: { z_out?: number } }[])[2].params?.z_out)
    .toBe(75);

  // A panel port leading to it.
  await page.locator(".react-flow__pane").click({ position: { x: 8, y: 8 } });
  const ports = page.getByTestId("build-panel-ports");
  await ports.getByRole("textbox").fill("MON");
  await ports.getByRole("combobox").click();
  await page.getByRole("option", { name: "output_stage.pin" }).click();
  await ports.getByRole("button", { name: "添加" }).click();
  await expect(ports.getByText("MON")).toBeVisible();
  await expect.poll(async () => JSON.stringify((await stored(request, id)).data.ports)).toContain("MON");

  // Delete it with the keyboard: its wire and panel port go with it.
  await page.locator('[data-name="output_stage"]').click();
  await page.keyboard.press("Delete");
  await expect(page.getByTestId("build-component")).toHaveCount(2);
  await expect(page.getByText("连带 1 条连线、1 个面板端口")).toBeVisible();
  await expect.poll(async () => JSON.stringify((await stored(request, id)).data.ports ?? {})).not.toContain("MON");
  doc = await stored(request, id);
  expect(doc.ok).toBe(true);

  // Every save is a version in the change log, written by the Web.
  await page.getByTestId("acceptance-tab-history").click();
  await expect(page.getByText("output_stage").first()).toBeVisible();
});

test("build area of a built-in model is read only and points at the copy (#1201)", async ({ page }) => {
  await page.goto(`/i/siglent-sds2104xplus?token=${TOKEN}`);
  await page.getByTestId("acceptance-tab-build").click();
  await expect(page.getByText("内置型号不能直接修改")).toBeVisible();
  await expect(page.getByTestId("build-library")).toHaveCount(0);
  await expect(page.getByTestId("build-component").first()).toBeVisible();
  await expect(page.getByTestId("build-panel-ports").getByRole("button", { name: "添加" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "复制为我的仪表" })).toBeVisible();
});

test("build area: declare a setting, bind a parameter to it, give it a command, try it (#1206)", async ({ page, request }) => {
  const copy = await request.post("/api/definitions/sb-oe-1/copy", { headers });
  const id = ((await copy.json()) as { data: { id: string } }).data.id;
  await page.goto(`/i/${id}?token=${TOKEN}`);
  await page.getByTestId("acceptance-tab-build").click();
  await expect(page.getByTestId("build-area")).toBeVisible();

  // A setting: out.zout, a real number in ohms.
  await page.getByTestId("build-tab-settings").click();
  const settings = page.getByTestId("build-settings");
  await settings.getByPlaceholder("设置名，如 out.level").fill("out.zout");
  await settings.getByRole("button", { name: "添加设置" }).click();
  const row = page.getByTestId("build-setting-out.zout");
  await expect(row).toBeVisible();
  await row.getByLabel("out.zout 单位").fill("OHM");
  await row.getByLabel("out.zout 单位").press("Enter");
  await row.getByLabel("out.zout 默认值").fill("50");
  await row.getByLabel("out.zout 默认值").press("Enter");
  await expect
    // Until the first setting is saved the copy has no state at all: read it as absent, poll on (#1665).
    .poll(async () => ((await stored(request, id)).data.state as Record<string, { unit?: string; default?: number }> | undefined)?.["out.zout"])
    .toMatchObject({ type: "float", unit: "OHM", default: 50 });

  // The output stage's impedance follows it.
  await page.locator('[data-name="out"]').click();
  await page.getByTestId("bind-out-z_out").click();
  await page.getByRole("option", { name: "out.zout" }).click();
  await expect(page.getByTestId("binding-out-z_out")).toContainText("out.zout");
  // A copy keeps the parent's component list and records the changed parameter alone (#1425 component_overrides).
  await expect
    .poll(async () => ((await stored(request, id)).data.component_overrides as Record<string, { params?: Record<string, unknown> }> | undefined)?.out?.params)
    .toEqual({ z_out: "$out.zout" });
  expect((await stored(request, id)).data.components).toBeUndefined();

  // A command for it.
  await page.locator(".react-flow__pane").click({ position: { x: 8, y: 8 } });
  await page.getByTestId("build-tab-commands").click();
  const commands = page.getByTestId("build-commands");
  const before = await commands.getByTestId("build-command").count();
  await commands.getByPlaceholder("命令头，如 :OUTPut:LEVel").fill(":OUTPut:IMPedance");
  await commands.getByRole("combobox", { name: "绑定到" }).click();
  await page.getByRole("option", { name: "out.zout" }).click();
  await commands.getByRole("button", { name: "添加命令" }).click();
  await expect(commands.getByTestId("build-command")).toHaveCount(before + 1);
  // A copy keeps the parent's command table and records the new command alone (D59 command_overrides).
  await expect.poll(async () => JSON.stringify((await stored(request, id)).data.command_overrides)).toContain(":OUTPut:IMPedance");
  expect((await stored(request, id)).data.commands).toBeUndefined();
  expect((await stored(request, id)).ok).toBe(true);

  // The try run answers with what was set.
  await page.getByTestId("acceptance-tab-try").click();
  const command = page.getByRole("combobox", { name: "命令" });
  await command.fill(":OUTPut:IMPedance 75");
  await page.getByRole("button", { name: "发送" }).click();
  await command.fill(":OUTPut:IMPedance?");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.locator("pre").last()).toContainText("7.5");
});

test("build area: rename follows the wires and ports, undo and redo, layout kept, try run beside (#1207)", async ({ page, request }) => {
  const copy = await request.post("/api/definitions/sb-oe-1/copy", { headers });
  const id = ((await copy.json()) as { data: { id: string } }).data.id;
  await page.goto(`/i/${id}?token=${TOKEN}`);
  await page.getByTestId("acceptance-tab-build").click();
  await expect(page.getByTestId("build-area")).toBeVisible();
  // The names it runs with: after an undo back to the model it copies, the file no longer writes them.
  const names = async () =>
    ((await (await request.get(`/api/definitions/${id}/structure`, { headers })).json()) as { components: { name: string }[] }).components.map((c) => c.name);

  // The test run, beside the canvas.
  await page.getByTestId("build-try-toggle").click();
  const drawer = page.getByTestId("build-try");
  await drawer.getByRole("combobox", { name: "命令" }).fill("*IDN?");
  await drawer.getByRole("button", { name: "发送" }).click();
  await expect(drawer.locator("pre").last()).toContainText("SB-OE-1");

  // Rename the output stage: its wire and its panel port follow.
  await page.locator('[data-name="out"]').click();
  const name = page.getByTestId("build-component-panel").getByRole("textbox", { name: "名称", exact: true });
  await name.fill("drive");
  await name.press("Enter");
  await expect.poll(names).toEqual(["orx", "drive"]);
  const doc = await stored(request, id);
  expect((doc.data.wiring as { instrument: string[][] }).instrument).toEqual([["orx.out", "drive.in"]]);
  expect((doc.data.ports as { instrument: Record<string, string> }).instrument).toMatchObject({ OUT: "drive.pin" });
  expect(doc.ok).toBe(true);
  // The test run reopened on the saved change.
  await expect(drawer.getByText("已按新保存的定义重开")).toBeVisible();

  // Undo with the button, redo with the keyboard: each is a saved version.
  await page.getByRole("button", { name: "撤销（Ctrl/⌘+Z）" }).click();
  await expect.poll(names).toEqual(["orx", "out"]);
  await page.locator(".react-flow__pane").click({ position: { x: 8, y: 8 } });
  await page.keyboard.press("Control+Shift+Z");
  await expect.poll(names).toEqual(["orx", "drive"]);

  // Drag a block: the position is kept beside the definition and survives a reload.
  await page.getByTestId("build-try-toggle").click(); // closed: the canvas has its full height again
  const node = page.locator('.react-flow__node[data-id="i:orx"]');
  const box = (await node.boundingBox())!;
  await page.mouse.move(box.x + 40, box.y + 12);
  await page.mouse.down();
  await page.mouse.move(box.x + 140, box.y + 92, { steps: 8 });
  await page.mouse.up();
  await expect
    .poll(async () => ((await (await request.get(`/api/definitions/${id}/layout`, { headers })).json()) as { positions: Record<string, unknown> }).positions)
    .toHaveProperty("i:orx");
  const moved = await node.getAttribute("style");
  await page.reload();
  await page.getByTestId("acceptance-tab-build").click();
  await expect(page.locator('.react-flow__node[data-id="i:orx"]')).toBeVisible();
  const transform = (style: string | null) => (style ?? "").split(";").find((p) => p.includes("transform"));
  expect(transform(await page.locator('.react-flow__node[data-id="i:orx"]').getAttribute("style"))).toBe(transform(moved));
});

test("build area: references and followed settings on the canvas, a grid for the rest, the channel zone while dragging (#1212)", async ({ page, request }) => {
  const id = await copy34465a(request);
  await page.goto(`/i/${id}?token=${TOKEN}`);
  await page.getByTestId("acceptance-tab-build").click();
  await expect(page.getByTestId("build-area")).toBeVisible();

  // The input names its signal conditioning: a dashed reference line with the parameter's name.
  await expect(page.locator(".react-flow__edge.build-ref").first()).toBeAttached();
  await expect(page.locator(".react-flow__edge-text", { hasText: "conditioning" }).first()).toBeVisible();
  // The A/D converter's calibration store, named by its store parameter (#1216).
  await expect(page.getByTestId("rf__edge-ref|i:meter|store")).toBeAttached();
  await expect(page.locator(".react-flow__edge-text", { hasText: "store" })).toBeVisible();
  // The A/D converter lists the settings it follows.
  const meter = page.locator('[data-name="meter"]');
  await expect(meter.getByTestId("build-follows")).toContainText("sense.nplc");

  // The blocks on no signal chain lie side by side, not in one column.
  const x = async (name: string) => (await page.locator(`[data-name="${name}"]`).boundingBox())!.x;
  expect(await x("trig")).not.toBe(await x("meter"));

  // Pointing at a setting lights what follows it and dims the rest.
  await page.getByTestId("build-tab-settings").click();
  await page.getByTestId("build-setting-sense.nplc").hover();
  await expect(meter).toHaveAttribute("data-lit", "true");
  await expect(page.locator('[data-name="trig"]')).toHaveAttribute("data-dim", "true");
  await page.getByTestId("build-tab-ports").click();
  await expect(meter).not.toHaveAttribute("data-lit", "true");

  // A single-channel meter shows no per-channel band; a drag shows the zone, a drop there makes a channel component.
  await expect(page.getByTestId("build-band")).toHaveCount(0);
  await expect(page.getByTestId("build-band-drop")).toHaveCount(0);
  await page.getByPlaceholder("按类型、名称或说明过滤").fill("input_port");
  const source = page.getByTestId("build-library-input_port");
  const from = (await source.boundingBox())!;
  await page.mouse.move(from.x + 20, from.y + 10);
  await page.mouse.down();
  await page.mouse.move(from.x + 300, from.y + 40, { steps: 6 });
  const zone = page.getByTestId("build-band-drop");
  await expect(zone).toBeVisible();
  const to = (await zone.boundingBox())!;
  await page.mouse.move(to.x + to.width / 2, to.y + to.height / 2, { steps: 6 });
  await page.mouse.up();
  await expect(page.getByTestId("build-band")).toBeVisible();
  await expect
    .poll(async () => ((await stored(request, id)).data.channel_components as { name: string }[] | undefined)?.map((c) => c.name))
    .toEqual(["input_port"]);
});

test("build area: grouped library, SI values, a table edited by row, one binding look, wide searchable lists, a note for the agent (#1214)", async ({ page, request, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  const id = await copy34465a(request);
  await page.goto(`/i/${id}?token=${TOKEN}`);
  await page.getByTestId("acceptance-tab-build").click();
  await expect(page.getByTestId("build-area")).toBeVisible();
  // The input terminal's parameters as the instrument runs with them (the copy inherits them until the first edit).
  const input = async () => {
    const res = await request.get(`/api/definitions/${id}/structure`, { headers });
    return ((await res.json()) as { components: { name: string; params?: Record<string, unknown> }[] }).components.find((c) => c.name === "input")?.params;
  };

  // The multimeter's own types come first (what its built-in models use); the other classes' types
  // are folded behind a button (P2-4). Opened, the library is grouped by family, each type with its ports.
  await expect(page.getByTestId("build-library-group-own")).toContainText("万用表型号用到的");
  await expect(page.getByTestId("build-library-dmm_adc")).toBeVisible();
  await expect(page.getByTestId("build-library-clock_recovery")).toHaveCount(0);
  await page.getByTestId("build-library-all").click();
  await expect(page.getByTestId("build-library-group-signal")).toBeVisible();
  await expect(page.getByTestId("build-library-group-scope")).toBeVisible();
  await expect(page.getByTestId("build-library-input_port")).toContainText("→ out");
  // Names first, the type as a grey tag, a sentence in Chinese (#1251); the search finds either.
  await expect(page.getByTestId("build-library-input_port")).toContainText("输入端口");
  await expect(page.getByTestId("build-library-input_port")).toContainText("input_port");
  await expect(page.getByTestId("build-library-clock_recovery")).toContainText("时钟恢复");
  await page.getByPlaceholder("按类型、名称或说明过滤").fill("时钟恢复");
  await expect(page.locator('[data-testid^="build-library-"][draggable]')).toHaveCount(1);
  await page.getByPlaceholder("按类型、名称或说明过滤").fill("clock_recovery");
  await expect(page.getByTestId("build-library-clock_recovery")).toBeVisible();
  await expect(page.getByTestId("build-library-input_port")).toHaveCount(0);
  await page.getByPlaceholder("按类型、名称或说明过滤").fill("");

  // A value reads and takes an SI prefix.
  await page.locator('[data-name="input"]').click();
  // The side panel: the name, the type, a sentence, and the English description folded away (#1251).
  const side = page.getByTestId("build-component-panel");
  await expect(side.getByRole("heading", { level: 2 })).toHaveText("万用表输入端子");
  await expect(side).toContainText("dmm_input_terminal");
  await expect(side.getByTestId("type-description")).toContainText("对地阻抗");
  await expect(side.getByTestId("type-details")).not.toHaveAttribute("open");
  await side.getByText("详细说明").click();
  await expect(side.getByTestId("type-details")).toHaveAttribute("open");
  await expect(side.getByTestId("type-details")).toContainText("The DMM's input terminals");
  const zin = page.locator("#param-input-z_in");
  await expect(zin).toHaveValue("10M");
  await zin.fill("20M");
  await zin.press("Enter");
  await expect.poll(async () => (await input())?.z_in).toBe(2e7);

  // A table parameter row by row: a new row keyed 10M.
  const table = page.getByTestId("table-param-input-test_current");
  await table.getByPlaceholder("新一行的键，如 10 或 1k").fill("10M");
  await table.getByRole("button", { name: "添加行" }).click();
  await expect.poll(async () => Object.keys(((await input())?.test_current ?? {}) as object)).toContain("10000000");

  // Bound and mapped parameters look the same; only the bound one can be unbound here.
  await expect(page.getByTestId("binding-input-high_z").getByRole("button", { name: "解除绑定" })).toBeVisible();
  await expect(page.getByTestId("binding-input-mode").getByRole("img", { name: "按映射" })).toBeVisible();

  // The side panel opens wide; the settings are searched and say who uses them.
  await page.locator(".react-flow__pane").click({ position: { x: 8, y: 8 } });
  await page.getByTestId("build-tab-settings").click();
  await page.getByTestId("build-wide").click();
  await expect(page.getByTestId("build-side")).toHaveAttribute("data-wide", "true");
  await page.getByPlaceholder("搜索设置名、类型或单位").fill("nplc");
  await expect(page.locator('[data-testid^="build-setting-sense"]')).toHaveCount(1);
  // Each row: the setting's name, its path as a grey tag (#1251); the name finds it as well.
  const nplcTitle = page.locator('[data-testid^="build-setting-sense"]').locator("[data-part=setting-title]");
  await expect(nplcTitle).toContainText("sense.nplc");
  const named = (await nplcTitle.innerText()).replace("sense.nplc", "").trim();
  expect(named.length).toBeGreaterThan(0);
  await page.getByPlaceholder("搜索设置名、类型或单位").fill(named);
  await expect(page.locator('[data-testid^="build-setting-sense.nplc"]')).toHaveCount(1);
  await page.getByPlaceholder("搜索设置名、类型或单位").fill("nplc");
  await expect(page.getByTestId("build-setting-users")).toContainText("meter");

  // The commands: searched, and what the Web leaves to the agent copied as a note.
  await page.getByTestId("build-tab-commands").click();
  await page.getByPlaceholder("搜索命令头或绑定").fill("NPLC");
  const nplc = page.getByTestId("build-command").first();
  await expect(nplc).toContainText("NPLC");
  await expect(nplc.getByRole("img", { name: "可写可查" })).toBeVisible();
  // Write only / query only / both: one of three, written as set/query.
  await nplc.getByRole("button").first().click();
  await nplc.getByTestId("build-command-access").getByRole("radio", { name: "只查" }).click();
  await expect(nplc.getByRole("img", { name: "只查" })).toBeVisible();
  await expect
    .poll(async () => JSON.stringify((await stored(request, id)).data.command_overrides ?? {}))
    .toContain('"set":false');
  await page.getByTestId("build-agent-note").click();
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toContain(`instruments/${id}.yaml`);
});

test("new instrument from the scope template, judged by the template's checks; identity edited in the build area (P2-5)", async ({ page, request }) => {
  await page.goto(`/?token=${TOKEN}`);
  await page.getByTestId("new-instrument-open").click();
  const dialog = page.getByTestId("new-instrument");
  await expect(dialog).toBeVisible();
  await expect(page.getByTestId("new-instrument-oscilloscope")).toHaveAttribute("aria-checked", "true");

  // The fields come from the template's schema, in the data sheet's language.
  const field = (pointer: string) => dialog.locator(`[data-pointer="/template/inputs/${pointer}"] input`).first();
  await field("identity/manufacturer").fill("Zhiyuan");
  await field("identity/manufacturer").press("Enter");
  await field("identity/model").fill("ZY-DSO3104");
  await field("identity/model").press("Enter");
  await expect(page.getByTestId("new-instrument-id")).toHaveValue("zhiyuan-zy-dso3104");
  transient.push("zhiyuan-zy-dso3104");

  // A table row out of order: the template's check refuses it at that cell, nothing is saved.
  const row = field("vertical/table/1/v_div");
  const before = await row.inputValue();
  await row.fill("0.0001");
  await row.press("Enter");
  await page.getByTestId("new-instrument-create").click();
  await expect(dialog.locator('[data-pointer="/template/inputs/vertical/table/1/v_div"]').getByTestId("new-instrument-problem")).toBeVisible();
  expect((await request.get("/api/definitions/zhiyuan-zy-dso3104/document", { headers })).status()).toBe(404);

  // Fixed (and 5 GSa/s typed with an SI prefix): created, and the acceptance page answers *IDN? as typed.
  await row.fill(before);
  await row.press("Enter");
  await field("adc/max_rate").fill("5G");
  await field("adc/max_rate").press("Enter");
  await page.getByTestId("new-instrument-create").click();
  await expect.poll(() => new URL(page.url()).pathname).toBe("/i/zhiyuan-zy-dso3104");
  await expect(page.getByText("Zhiyuan,ZY-DSO3104,AC2040001,2.1.0")).toBeVisible();
  await expect(page.getByTestId("acceptance-provenance")).toHaveText("自建/接口契约");
  expect(((await stored(request, "zhiyuan-zy-dso3104")).data.template as { inputs: { adc: { max_rate: number } } }).inputs.adc.max_rate).toBe(5e9);

  // A copy: the identity page writes only the serial it changes, and the badge says what it is.
  const id = await copy34465a(request);
  await page.goto(`/i/${id}?token=${TOKEN}`);
  await expect(page.getByTestId("acceptance-provenance")).toHaveText("基于 keysight-34465a（未改动）");
  await page.getByTestId("acceptance-tab-build").click();
  await page.getByTestId("build-tab-identity").click();
  const serial = page.getByTestId("build-identity").getByLabel("序列号");
  await serial.fill("MY99000001");
  await serial.press("Enter");
  await expect.poll(async () => (await stored(request, id)).data.identity).toEqual({ serial: "MY99000001" });
  await expect(page.getByTestId("acceptance-provenance")).toHaveText("基于 keysight-34465a · 已改动");
});
