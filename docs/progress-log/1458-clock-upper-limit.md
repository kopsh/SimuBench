## 虚拟时间有上限：极大的推进被拒绝，会话保持可用（Issue 1458）

| 完成条件 | 自检 |
| --- | --- |
| 规定虚拟时间的上限，单次与累计都受约束 | ✅ `engine/clock.py`：`MAX_VIRTUAL_NS = 2**53`（约 104 天，JSON/JavaScript 精确、远低于 int64）；显式 `advance` 会越过上限时抛 `ClockLimit`（`RuntimeError`，HTTP 409），时钟不动；调用方无法拒绝的步进（命令耗时、空闲）停在上限；D47 的条目补了一条 |
| HTTP `seconds` 加上限，超出 422 | ✅ `AdvanceRequest.seconds`：`gt=0`、`le=MAX_ADVANCE_S`（≈ 9007199 s）；`1e308`、`1e10`、`"inf"`、`"nan"`、`0` 都是 422（`tests/test_server.py`）；`schemas/openapi.json` 与 web 类型带上限与说明。MCP `advance_clock` 仍是每次 ≤ 3600 s，累计越界同样回错 |
| 拒绝后会话保持可用 | ✅ 同一个测试：推进 9e6 s 后再推 9e6 s 得 409、`now_ns` 不变，示波器 `*IDN?` 与 `/screen` 照常；issue 里的 DSO-X 复现（推进后 `:MEAS:VPP?`）不再出现 -300 |
| `wall` / `scaled` 长时间运行受保护 | ✅ 实时时钟追赶真实时间时取 `min(…, MAX_VIRTUAL_NS)`，到上限就停（`tests/test_scaled_clock.py`：1e15 虚拟秒后停在上限，再走也不动），没有 -300 |
