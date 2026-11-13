// A scripted OpenAI-compatible LLM service for the assistant's end-to-end test (#877): streamed
// Chat Completions with tool calls, decided from the conversation it is sent. The real SimuBench
// server talks to it exactly as to a private deployment (configured through the settings dialog).
import { createServer } from "node:http";

const port = Number(process.argv[2] ?? "18766");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function plan(body) {
  const messages = body.messages ?? [];
  const tools = (body.tools ?? []).map((t) => t.function?.name);
  const lastUser = messages.findLastIndex((m) => m.role === "user");
  const userText = String(messages[lastUser]?.content ?? "");
  const since = messages.slice(lastUser + 1).filter((m) => m.role === "tool");
  const parse = (m) => {
    try {
      return JSON.parse(m.content);
    } catch {
      return {};
    }
  };
  const latest = since.length ? parse(since.at(-1)) : {};
  const sha = [...since].reverse().map(parse).find((r) => r.sha256)?.sha256;
  if (tools.includes("ping")) return since.length ? { text: ["OK"] } : { call: ["ping", {}] };
  if (userText.includes("示波器")) {
    if (since.length === 0) return { call: ["read_bench", {}] };
    if (since.length === 1 && latest.running) return { call: ["ask_to_stop_session", { reason: "添加仪表要先停止测试台" }] };
    return { text: ["添加示波器需要先", "停止测试台，", "可以吗？"] };
  }
  if (userText.includes("同意停止")) {
    const step = since.length;
    if (step === 0) return { call: ["stop_session", {}] };
    if (step === 1) return { call: ["read_bench", {}] };
    if (step === 2) return { call: ["edit_bench", { base_sha256: sha, ops: [{ op: "add_instrument", name: "scope2", model: "sb-dso-4" }] }] };
    if (step === 3) return { call: ["start_session", {}] };
    return { text: ["已添加示波器 scope2，", "并重新启动了测试台。"] };
  }
  if (userText.includes("标签")) {
    const step = since.length;
    if (step === 0) return { call: ["read_bench", {}] };
    if (step === 1) return { call: ["edit_bench", { base_sha256: sha, ops: [{ op: "set_label", name: "awg", label: "AI 改的" }] }] };
    return { text: ["已把信号源的标签改为 AI 改的。"] };
  }
  // the same failing edit again and again: the server stops the turn for going in circles (#899)
  if (userText.includes("ghost")) {
    if (since.length === 0) return { call: ["read_bench", {}] };
    return { call: ["edit_bench", { base_sha256: sha, ops: [{ op: "remove", name: "ghost" }] }] };
  }
  // big tool results until the context is compacted (#899)
  if (userText.includes("目录")) {
    const ids = ["siglent-sdg2042x", "siglent-sds2104xplus", "rigol-dl3021", "keysight-33622a", "fluke-5522a", "keysight-34465a", "keysight-53220a", "keysight-53230a", "bk-8614"];
    const step = since.length;
    if (step < ids.length) return { call: ["get_definition", { definition_id: ids[step] }] };
    return { text: ["看完了，目录里的仪表不少。"] };
  }
  if (userText.includes("慢")) return { text: Array.from({ length: 60 }, (_, i) => `第${i + 1}句。`), delay: 400 };
  return { text: ["你好，", "我是 SimuBench 助手。"] };
}

const base = { id: "fake", object: "chat.completion.chunk", created: 0, model: "fake-model" };
const chunk = (delta, finish = null) => `data: ${JSON.stringify({ ...base, choices: [{ index: 0, delta, finish_reason: finish }] })}\n\n`;

createServer(async (req, res) => {
  if (req.method !== "POST" || !req.url?.endsWith("/chat/completions")) {
    res.writeHead(404).end();
    return;
  }
  let raw = "";
  for await (const part of req) raw += part;
  const step = plan(JSON.parse(raw));
  res.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-cache" });
  if (step.call) {
    const [name, args] = step.call;
    res.write(chunk({ role: "assistant", tool_calls: [{ index: 0, id: `call-${Date.now()}`, type: "function", function: { name, arguments: "" } }] }));
    res.write(chunk({ tool_calls: [{ index: 0, function: { arguments: JSON.stringify(args) } }] }));
    res.write(chunk({}, "tool_calls"));
  } else {
    res.write(chunk({ role: "assistant", content: "" }));
    for (const piece of step.text) {
      if (res.destroyed) return;
      await sleep(step.delay ?? 40);
      res.write(chunk({ content: piece }));
    }
    res.write(chunk({}, "stop"));
  }
  res.end("data: [DONE]\n\n");
}).listen(port, "127.0.0.1");
