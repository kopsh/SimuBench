// Issue 31: no "run" step — a bench opens ready to use; close, reset and switch replace it.
import { expect, type Page, test } from "@playwright/test";
import { createConnection } from "node:net";

const TOKEN = "e2e-token";
const headers = { "x-simubench-token": TOKEN };

async function openBench(page: Page, name: string) {
  await page.getByRole("listitem").filter({ hasText: name }).getByRole("button", { name: "打开测试台" }).click();
  await expect(page.locator(".react-flow__node").first()).toBeVisible();
}

test("a bench opens ready to use, and closing it frees the workspace", async ({ page }) => {
  await page.goto(`/?token=${TOKEN}`);
  await openBench(page, "awg_rc_scope");
  await expect(page.getByTestId("session-activity")).toBeVisible(); // the clock state, not "running"
  // An instrument answers right away: the bench is usable the moment it opens.
  const sid = page.url().split("/s/")[1]?.split("?")[0];
  const scpi = await page.request.post(`/api/sessions/${sid}/instruments/scope/scpi`, {
    headers,
    data: { command: "*IDN?" },
  });
  expect(((await scpi.json()) as { response: string }).response).toContain("SB-DSO-4");

  await page.getByRole("button", { name: "关闭测试台" }).click();
  await expect(page).toHaveURL(/\/$/); // back home
  await expect(page.getByTestId("script-access")).toHaveCount(0); // the ports are free again
});

test("reset brings instruments back to power-on defaults", async ({ page }) => {
  await page.goto(`/?token=${TOKEN}`);
  await openBench(page, "awg_rc_scope");
  const sid = page.url().split("/s/")[1]?.split("?")[0];
  await page.request.post(`/api/sessions/${sid}/instruments/scope/configure`, {
    headers,
    data: { path: "ch1.scale", value: 0.5 },
  });

  await page.getByRole("button", { name: "重置" }).click();
  await expect(page.getByText("已重置：仪表回到初始状态")).toBeVisible();
  const info = (await (
    await page.request.get(`/api/sessions/${sid}`, { headers })
  ).json()) as { instruments: { name: string; state: { path: string; value: number }[] }[] };
  const scale = info.instruments
    .find((i) => i.name === "scope")!
    .state.find((s) => s.path === "ch1.scale")!;
  expect(scale.value).not.toBe(0.5);
  await page.getByRole("button", { name: "关闭测试台" }).click(); // leave the workspace free
  await expect(page).toHaveURL(/\/$/);
});

test("switching benches asks first while a script is connected", async ({ page }) => {
  await page.goto(`/?token=${TOKEN}`);
  await openBench(page, "awg_rc_scope");
  const sid = page.url().split("/s/")[1]?.split("?")[0];
  const info = (await (
    await page.request.get(`/api/sessions/${sid}`, { headers })
  ).json()) as { instruments: { scpi_port: number | null }[] };
  const port = info.instruments.find((i) => i.scpi_port)?.scpi_port;
  expect(port).toBeTruthy();
  const script = createConnection({ host: "127.0.0.1", port: port! }); // a script holds the port
  await new Promise<void>((resolve) => script.once("connect", resolve));
  await expect
    .poll(async () => ((await (await page.request.get(`/api/sessions/${sid}`, { headers })).json()) as { clients: unknown[] }).clients.length)
    .toBeGreaterThan(0); // the session lists the script before the UI relies on it

  await page.goto(`/?token=${TOKEN}`);
  await expect(page.getByTestId("script-access")).toBeVisible(); // the open bench says so
  await page.getByRole("listitem").filter({ hasText: "lockin_rc" }).getByRole("button", { name: "打开测试台" }).click();
  await expect(page.getByText("切换测试台？")).toBeVisible();
  await expect(page.getByText(/socket:127\.0\.0\.1:/)).toBeVisible(); // the script is named
  await page.getByRole("button", { name: "取消" }).click(); // keep the connected bench
  await expect(page.getByTestId("script-access").first()).toBeVisible();

  await page.getByRole("listitem").filter({ hasText: "lockin_rc" }).getByRole("button", { name: "打开测试台" }).click();
  await page.getByRole("button", { name: "仍要切换" }).click();
  await expect(page.locator(".react-flow__node").first()).toBeVisible();
  script.destroy();
});

test("an invalid definition cannot switch back to Operate", async ({ page }) => {
  const path = `e2e/invalid-${Date.now()}.bench.yaml`;
  const bench = {
    schema: "simubench.bench/1",
    name: "invalid-e2e",
    instruments: { awg: { model: "sb-awg-2" } },
    duts: {},
    wires: [],
  };
  await page.request.put("/api/benches/file", { headers, data: { path, base_sha256: null, data: bench } });
  const sid = ((await (
    await page.request.post("/api/sessions", { headers, data: { bench_path: path } })
  ).json()) as { id: string }).id;
  await page.goto(`/s/${sid}?token=${TOKEN}`);
  await page.getByRole("radio", { name: "搭建" }).click();
  // Break the definition on disk, then try to leave Build.
  bench.instruments.awg.model = "no-such-model";
  const doc = (await (
    await page.request.get(`/api/benches/file?path=${encodeURIComponent(path)}`, { headers })
  ).json()) as { sha256: string };
  await page.request.put("/api/benches/file", { headers, data: { path, base_sha256: doc.sha256, data: bench } });
  await expect(page.getByText("已载入在其他地方所做的修改")).toBeVisible(); // the editor picked it up
  await page.getByRole("radio", { name: "操作" }).click();
  await expect(page.getByText("搭建中的定义有问题，无法切回操作")).toBeVisible();
  await expect(page.getByRole("tab", { name: "元件库" })).toBeVisible(); // still in Build
});

test("only opening a bench writes no record", async ({ page }) => {
  await page.goto(`/?token=${TOKEN}`);
  const before = (await (await page.request.get("/api/runs", { headers })).json()).length;
  await openBench(page, "specan");
  const sid = page.url().split("/s/")[1]?.split("?")[0];
  const opened = (await (
    await page.request.get(`/api/sessions/${sid}`, { headers })
  ).json()) as { instruments: { name: string }[] };
  await page.request.post(`/api/sessions/${sid}/instruments/${opened.instruments[0].name}/scpi`, {
    headers,
    data: { command: "*IDN?" },
  });
  await expect
    .poll(async () => (await (await page.request.get("/api/runs", { headers })).json()).length)
    .toBe(before + 1); // the first command starts the record
  await page.getByRole("button", { name: "关闭测试台" }).click();
});
