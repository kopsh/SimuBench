// Issue 121: while a script is connected, the UI does not change the instruments behind its back.
import { type APIRequestContext, expect, type Page, test } from "@playwright/test";
import { type Socket, createConnection } from "node:net";

const TOKEN = "e2e-token";
const headers = { "x-simubench-token": TOKEN };

const BENCH = {
  schema: "simubench.bench/2",
  name: "remote-e2e",
  instruments: {
    awg: { model: "sb-awg-2", label: "函数发生器" },
    scope: { model: "sb-dso-4", label: "示波器" },
  },
  parts: { rc: { type: "rc_lowpass", label: "RC 低通", params: { r: 1000, c: 1e-7 } } },
  wires: [
    { from: "awg.OUT1", to: "rc.IN" },
    { from: "rc.OUT", to: "scope.CH1", accessories: [{ type: "passive_probe" }] },
  ],
  layout: { awg: { x: 0, y: 80 }, rc: { x: 360, y: 100 }, scope: { x: 640, y: 40 } },
};

type Info = { clients: unknown[]; instruments: { name: string; scpi_port: number | null }[] };

const session = async (request: APIRequestContext, sid: string) =>
  (await (await request.get(`/api/sessions/${sid}`, { headers })).json()) as Info;

/** A fresh bench file and a session on it, opened in Operate. */
async function openBench(page: Page): Promise<{ path: string; sid: string }> {
  const path = `e2e/remote-${Date.now()}.bench.yaml`;
  await page.request.put("/api/benches/file", { headers, data: { path, base_sha256: null, data: BENCH } });
  const start = await page.request.post("/api/sessions", { headers, data: { bench_path: path, force: true } });
  const sid = ((await start.json()) as { id: string }).id;
  await page.goto(`/s/${sid}?token=${TOKEN}`);
  await expect(page.locator(".react-flow__node")).toHaveCount(3);
  return { path, sid };
}

/** A script on the scope's raw socket port, once the session lists it. */
async function connectScript(page: Page, sid: string): Promise<Socket> {
  const port = (await session(page.request, sid)).instruments.find((i) => i.name === "scope")!.scpi_port!;
  const script = createConnection({ host: "127.0.0.1", port });
  await new Promise<void>((resolve) => script.once("connect", resolve));
  await expect.poll(async () => (await session(page.request, sid)).clients.length).toBeGreaterThan(0);
  return script;
}

async function send(script: Socket, command: string): Promise<string> {
  return new Promise((resolve) => {
    script.once("data", (d) => resolve(d.toString()));
    script.write(`${command}\n`);
  });
}

test("a script makes the instrument remote; LOCAL takes it back until the script's next command", async ({ page }) => {
  const { sid } = await openBench(page);
  const script = await connectScript(page, sid);
  await send(script, ":CHAN1:SCAL 0.2;:CHAN1:SCAL?");

  await page.locator(".react-flow__node").filter({ hasText: "示波器" }).click();
  const banner = page.getByTestId("remote-banner").first();
  await expect(banner).toContainText("远程控制中");
  await expect(banner).toContainText(/socket:127\.0\.0\.1:/);

  // The console is refused while remote, and says how to take the instrument back.
  await page.getByLabel("发送 SCPI 命令").fill(":CHAN1:SCAL 0.5");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByText("scope 由脚本远程控制")).toBeVisible();
  expect(parseFloat(await send(script, ":CHAN1:SCAL?"))).toBeCloseTo(0.2); // the script's setting stands

  await banner.getByRole("button", { name: "本地（LOCAL）" }).click();
  await expect(banner).toContainText("本地操作中");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByRole("status", { name: "响应" })).toContainText("✓");

  await send(script, "*IDN?"); // the script's next command: remote again
  await expect(banner).toContainText("远程控制中");

  script.destroy();
  await expect.poll(async () => (await session(page.request, sid)).clients.length).toBe(0);
  await expect(page.getByTestId("remote-banner")).toHaveCount(0);
});

test("with a script connected, Operate asks before rebuilding and can rebuild later", async ({ page }) => {
  const { path, sid } = await openBench(page);
  const script = await connectScript(page, sid);
  await page.getByRole("radio", { name: "搭建" }).click();

  // A topology change made elsewhere (an agent): the next Operate would rebuild the bench.
  const doc = (await (await page.request.get(`/api/benches/file?path=${encodeURIComponent(path)}`, { headers })).json()) as {
    sha256: string;
  };
  const changed = { ...BENCH, parts: { ...BENCH.parts, r: { type: "resistor", params: { r: 1000 } } } };
  await page.request.put("/api/benches/file", { headers, data: { path, base_sha256: doc.sha256, data: changed } });
  await expect(page.getByText("已载入在其他地方所做的修改")).toBeVisible();

  await page.getByRole("radio", { name: "操作" }).click();
  await expect(page.getByText("按新的定义重建？")).toBeVisible();
  await expect(page.getByRole("list", { name: "已连接的脚本" })).toContainText(/socket:127\.0\.0\.1:/);
  await page.getByRole("button", { name: "稍后重建" }).click();

  // Operate on the running bench: the script keeps its connection, the top bar offers the rebuild.
  await expect(page.getByRole("tab", { name: "元件库" })).toHaveCount(0);
  expect((await session(page.request, sid)).clients.length).toBeGreaterThan(0);
  expect(await send(script, "*IDN?")).toContain("SB-DSO-4");

  await page.getByRole("button", { name: "测试台文件已修改" }).click();
  await expect(page.getByRole("dialog", { name: "按新的定义重建？" })).toBeVisible();
  await page.getByRole("button", { name: "断开并重建" }).click();
  await expect(page.getByText("已按新的测试台定义重建")).toBeVisible();
  await expect(page.locator(".react-flow__node")).toHaveCount(4);
  await expect(page.getByRole("button", { name: "测试台文件已修改" })).toHaveCount(0);
  script.destroy();
});
