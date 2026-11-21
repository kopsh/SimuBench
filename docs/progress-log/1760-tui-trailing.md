## TUI 会话刷新直创路径并入 trailing 合并协议，sid 尾随重读不再可丢（Issue 1760）

| 完成条件 | 自检 |
| --- | --- |
| `_on_event` 对 session/remote/clock 事件的直创 `create_task(_refresh_session())` 改经 `_refresh_session_soon()`（与 sid 事件同一 trailing 协议，docstring 的 never-dropped 不变式对两条流都成立） | ✅ `src/simubench/tui/app.py` 一行改路由；`tests/test_tui_units.py::test_rapid_sid_events_collapse_into_one_trailing_refresh`（既有）原样过 |
| 回归测试：session 流事件在飞时工作区流 sid 事件到达 → 恰好两次读（尾随不丢）；快速 session 流事件合并（初始 1 次 + 单次尾随） | ✅ `test_a_session_stream_event_keeps_the_sid_events_trailing_reread`、`test_rapid_session_stream_events_collapse_too`——两用例对还原 app.py 的旧代码实测红（直创任务不消费 `_session_queued`，calls==1 丢尾随；三直创 calls==3 无合并） |
| TUI 测试全过 | ✅ `uv run pytest tests/test_tui_units.py tests/test_tui.py -q` 143 过 |
