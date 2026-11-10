import { expect, type Page, test } from "@playwright/test";

// The AI assistant end to end (#877): the real service talks to a scripted OpenAI-compatible LLM
// (e2e/fake-llm.mjs, started by serve.mjs on the next port), configured through the settings
// dialog. Not configured → configure and test → a turn that has to ask before stopping the bench →
// the next turn stops, edits, restarts (the page follows the new session) → restore to before the
// last turn → stop a streaming turn → clear → the panel on a narrow window.

const TOKEN = "e2e-token";
const headers = { "x-simubench-token": TOKEN };
const LLM = "http://127.0.0.1:18766/v1";
const BENCH = {
  schema: "simubench.bench/2",
  name: "assistant-e2e",
  instruments: { awg: { model: "sb-awg-2" }, scope: { model: "sb-dso-4" } },
  wires: [{ from: "awg.OUT1", to: "scope.CH1" }],
  layout: { awg: { x: 0, y: 80 }, scope: { x: 420, y: 40 } },
};

async function openBench(page: Page): Promise<string> {
  const path = `e2e/assistant-${Date.now()}.bench.yaml`;
  expect((await page.request.put("/api/benches/file", { headers, data: { path, base_sha256: null, data: BENCH } })).ok()).toBeTruthy();
  const sid = ((await (await page.request.post("/api/sessions", { headers, data: { bench_path: path } })).json()) as { id: string }).id;
  await page.goto(`/s/${sid}?token=${TOKEN}`);
  await expect(page.locator(".react-flow__node")).toHaveCount(2);
  return path;
}

/** Send a message once the previous turn has ended (the composer offers 发送 again, not 停止). */
async function say(page: Page, text: string) {
  const panel = page.getByTestId("assistant-panel");
  await expect(panel.getByRole("button", { name: "停止" })).toHaveCount(0, { timeout: 15_000 });
  const box = panel.getByRole("textbox", { name: /说说要搭什么测试台/ });
  await box.fill(text);
  await expect(panel.getByRole("button", { name: "发送" })).toBeEnabled();
  await box.press("Enter");
}

test("configure, build with consent to stop, restore, stop, clear, narrow", async ({ page }) => {  test.setTimeout(90_000);
  await page.request.put("/api/assistant/config", { headers, data: { reset: true } });
  const path = await openBench(page);
  const panel = page.getByTestId("assistant-panel");

  // not configured: the panel points to the settings
  await openPanel(page);
  await expect(panel.getByText("还没有配置 AI 助手")).toBeVisible();
  await panel.getByRole("button", { name: "去设置" }).click();
  await page.getByLabel("服务地址").fill(LLM);
  await page.getByLabel("模型").fill("fake-model");
  await page.getByLabel("API key").fill("sk-e2e-secret");
  await page.getByRole("button", { name: "测试连接" }).click();
  await expect(page.getByTestId("assistant-test-result")).toContainText("连接成功");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  const config = await (await page.request.get("/api/assistant/config", { headers })).text();
  expect(config).not.toContain("sk-e2e-secret");
  expect(config).toContain('"api_key_set":true');

  // a structural change while the bench runs: the assistant asks first
  await say(page, "加一台示波器");
  await expect(panel.getByText("正在读取测试台")).toBeVisible();
  await expect(panel.getByText("添加示波器需要先停止测试台，可以吗？")).toBeVisible();
  expect(Object.keys(((await (await page.request.get(`/api/benches/file?path=${path}`, { headers })).json()) as { data: { instruments: object } }).data.instruments)).toEqual(["awg", "scope"]);

  // agreed: it stops, adds, restarts, and the page follows the bench to its new session
  const before = page.url();
  await say(page, "可以");
  await expect(panel.getByText("已添加示波器 scope2，并重新启动了测试台。")).toBeVisible({ timeout: 20_000 });
  await expect(panel.getByTestId("assistant-summary").last()).toContainText("scope2");
  await expect.poll(() => page.url()).not.toBe(before);
  await expect(page.locator(".react-flow__node")).toHaveCount(3);

  // restore to before the last turn: the running bench uses the file, so it stops first
  await panel.getByRole("button", { name: /恢复到上一轮之前/ }).click();
  const plan = page.getByTestId("rollback-plan");
  await expect(plan).toContainText(path);
  await expect(plan).toContainText("需要先停止");
  await page.getByRole("button", { name: "停止并恢复" }).click();
  await expect(page.getByText("已恢复到上一轮之前").first()).toBeVisible();
  await expect(page.getByTestId("bench-stopped")).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: "启动测试台" }).click();
  await expect(page.locator(".react-flow__node")).toHaveCount(2);
  await expect(panel.getByRole("button", { name: /恢复到上一轮之前/ })).toHaveCount(0);

  // a streaming answer can be stopped
  await say(page, "慢慢讲");
  await expect(panel.getByText("第2句。", { exact: false })).toBeVisible();
  await panel.getByRole("button", { name: "停止" }).click();
  await expect(panel.getByText(/已停止/)).toBeVisible();

  // clear the conversation
  await panel.getByRole("button", { name: "清除对话" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "清除对话" }).click();
  await expect(panel.getByText("在这里用对话搭建测试台")).toBeVisible();

  // a narrow window: the panel covers the page, and closes
  await page.setViewportSize({ width: 600, height: 800 });
  const box = await panel.boundingBox();
  expect(box?.width).toBeGreaterThan(560);
  await panel.getByRole("button", { name: "收起 AI 助手" }).click();
  await expect(panel).toHaveCount(0);
});

/** Configure through the API (the dialog path is covered by the big test above). */
async function configureLLM(page: Page, extra: Record<string, unknown> = {}) {
  await page.request.put("/api/assistant/config", { headers, data: { reset: true } });
  const saved = await page.request.put("/api/assistant/config", {
    headers,
    data: { base_url: LLM, model: "fake-model", api_key: "sk-e2e-secret", ...extra },
  });
  expect(saved.ok()).toBeTruthy();
}

/** Open the panel unless it is already open (the open state survives a page change). */
async function openPanel(page: Page) {
  const panel = page.getByTestId("assistant-panel");
  if ((await panel.count()) === 0) {
    await page.getByRole("button", { name: "AI 助手", exact: true }).click();
  }
  await expect(panel).toBeVisible();
}

test("a hand-edited file is listed as lost; cancel sends nothing; confirm restores", async ({ page }) => {
  test.setTimeout(60_000);
  await configureLLM(page);
  const path = await openBench(page);
  const panel = page.getByTestId("assistant-panel");
  await openPanel(page);

  // a turn that writes the file
  await say(page, "把函数发生器的标签改成 AI 改的");
  await expect(panel.getByText(/已把信号源的标签/)).toBeVisible({ timeout: 20_000 });

  // the user edits the file by hand afterwards: restoring would lose that
  const sid = page.url().split("/s/")[1]!.split("?")[0]!;
  await page.request.delete(`/api/sessions/${sid}`, { headers });
  type Doc = { sha256: string; data: { instruments: Record<string, { model: string; label?: string }> } };
  const doc = (await (await page.request.get(`/api/benches/file?path=${path}`, { headers })).json()) as Doc;
  doc.data.instruments.awg.label = "手改的";
  expect(
    (await page.request.put("/api/benches/file", { headers, data: { path, base_sha256: doc.sha256, data: doc.data } })).ok(),
  ).toBeTruthy();

  // the dialog lists the file and the change that would be lost
  await panel.getByRole("button", { name: /恢复到上一轮之前/ }).click();
  const plan = page.getByTestId("rollback-plan");
  await expect(plan).toContainText(path);
  await expect(plan).toContainText("在那之后又被改过");
  await expect(plan).toContainText("手改的");

  // cancel: no rollback request goes out
  const posts: string[] = [];
  await page.route("**/api/assistant/rollback", (route) => {
    if (route.request().method() === "POST") posts.push(route.request().postData() ?? "");
    return route.continue();
  });
  await page.getByRole("dialog").getByRole("button", { name: "取消" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(posts).toEqual([]);

  // confirm: the consent is in the request body, and the file is as it was before the turn
  await panel.getByRole("button", { name: /恢复到上一轮之前/ }).click();
  await expect(page.getByTestId("rollback-plan")).toContainText("手改的");
  await page.getByRole("dialog").getByRole("button", { name: "恢复", exact: true }).click();
  await expect(page.getByText("已恢复到上一轮之前").first()).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(posts).toHaveLength(1);
  const body = JSON.parse(posts[0]!) as { turn_id: string; confirm_lost: boolean; stop_sessions: boolean };
  expect(body.turn_id).toBeTruthy();
  expect(body.confirm_lost).toBe(true);
  expect(body.stop_sessions).toBe(true);
  const after = (await (await page.request.get(`/api/benches/file?path=${path}`, { headers })).json()) as Doc;
  expect(after.data.instruments.awg.label).toBeUndefined();
  expect(after.data.instruments.awg.model).toBe("sb-awg-2");
  await expect(panel.getByRole("button", { name: /恢复到上一轮之前/ })).toHaveCount(0);
});

test("going in circles stops the turn in words, and compaction leaves a note", async ({ page }) => {
  test.setTimeout(90_000);
  await configureLLM(page);
  await openBench(page);
  const panel = page.getByTestId("assistant-panel");
  await openPanel(page);

  // the same failing edit again and again: the server stops the turn, the reason is in words
  await say(page, "把 ghost 删掉");
  const note = panel.getByText(/助手停下了/);
  await expect(note).toBeVisible({ timeout: 20_000 });
  const reason = (await note.textContent()) ?? "";
  expect(reason).toContain("没有进展");
  expect(reason).not.toContain("{"); // said in words, not a JSON dump
  await expect(panel.getByRole("button", { name: /恢复到上一轮之前/ })).toHaveCount(0);
  await say(page, "你好"); // the conversation goes on after a stopped turn
  await expect(panel.getByText(/我是 SimuBench 助手/)).toBeVisible({ timeout: 15_000 });

  // big tool results over a small configured window: the older ones are compacted, the panel notes it
  await configureLLM(page, { context_tokens: 1000 });
  await openBench(page);
  await openPanel(page);
  await say(page, "看看目录里的仪表");
  await expect(panel.getByText(/更早的内容已压缩/)).toBeVisible({ timeout: 30_000 });
  await expect(panel.getByText(/看完了/)).toBeVisible({ timeout: 20_000 });
  await say(page, "谢谢"); // the conversation still works on the compacted history
  await expect(panel.getByText(/你好，/)).toBeVisible({ timeout: 15_000 });
});

