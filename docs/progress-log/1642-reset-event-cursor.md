## 会话重置后事件序号接着数（Issue 1642）

| 完成条件 | 自检 |
| --- | --- |
| 重置前的最大 seq 作为 `since` 连接，新会话的事件必须收到 | ✅ `EventBus(first_id)`：`manager.reset` 把旧会话最后的 `eid` 经 `start` → `open_bench` → `Session(first_event_id)` 传给新总线，新会话的事件从旧 `eid + 1` 起；`tests/test_server.py::test_event_cursor_survives_a_reset`（重置后最小 eid = 旧最大 + 1，`since=旧最大` 收到新会话全部事件；去掉修复时该断言失败） |
| Web 单测：重置后重连，日志不缺事件 | ✅ `web/src/api/events.test.ts`「重置后重连不缺事件」：重连带 `since=10`，新会话的 11、12 进日志，再重连带 `since=12`；Web 客户端代码不用改 |
| D91 补「重置后游标」的裁决 | ✅ D91 结论、理由、影响各补一句：重置不让游标回头，没有采用「`since` 超过最大 eid 就全量」（新会话追上旧游标后仍丢前 N 条）与会话代号（要改事件格式）。MCP `events` 的 `after`/`next` 游标同受益；TUI `store.clear()` 复位仍全量重放，不受影响 |
| 未能核实：重置时 Web 旧 socket 是否关闭并重连 | 未做 e2e。不再重要：无论旧 socket 是否重连、何时重连，游标都单调，两种路径都不缺事件 |
