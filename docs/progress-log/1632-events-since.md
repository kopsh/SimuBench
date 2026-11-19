## 会话事件流加 since 游标：重连只补尾增量，Web 通信日志不再整段重复（Issue 1632）

| 完成条件 | 自检 |
| --- | --- |
| 服务器接受 `since=<seq>`，只发 seq > since（重放与活事件同规则）；缺省 0 = 全历史 | ✅ `server/routes/events.py` 的 `cursor(ev)` 以引擎 eid 为流序号（按连接重编号的 counter 降为无 eid 事件的缺省），`push` 与 backlog 重放同按 since 过滤；`tests/test_server.py::test_event_stream_since_sends_only_the_tail` 走一条流：缺省连接收全历史、重连带 since 只收尾、流从游标继续；`test_event_stream_rejects_a_bad_since` 锁非整数 since 关 1008 |
| 断线重连后 Web 通信日志不重复 | ✅ `web/src/api/events.ts` 的 `useEventStream` 记下收到的最后一个 seq，重连 URL 带 `&since=<seq>`（首次连接不带）；`web/src/api/events.test.ts` 以假 WebSocket 钉住：重连 URL 带 since、旧事件不再到达、日志恰好 [1,2,3]、游标随日志前进、D64 的重连 bump 保留 |
| 大历史会话重连只传增量 | ✅ 上述 flow 测试里 since 连接的第一个事件就是游标+1（有积压时不再从头重放）；服务器测试与 Web 测试各按各自协议位置断言 |
| 现有 TUI store 行为不变；Web 现有事件测试原样存活 | ✅ `EventStore` 的 add/add_many 去重、bounded、dropped 计数语义未动（`tests/test_tui_units.py` 两处 store 测试原断言全保留，只更新过时注释并补 `max_seq` 断言）；web 全量 vitest 395 过 |
| TUI 的去重机件退役或降级为纯防御 | ✅ 降级为纯防御：连接循环重连传 `store.max_seq`（`ws_url` 增 since 参；`clear()` 复位后回 0，重建会话仍全量重放），docstring 改述「服务器发尾增量，去重是兜底」 |
| seq 语义变化与 since 参数写新 D 条目；progress-log 一条一文件 | ✅ D91（会话内稳定游标 = 引擎 eid、since 语义、两客户端取舍、工作区流不在范围）写入 docs/decisions.md；本文件即 progress-log 条目 |
