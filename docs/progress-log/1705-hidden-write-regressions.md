## `set_parameter` 与 `hidden` 故障写入的 D9 遮蔽回归，`hidden` 事件核对结论（Issue 1705）

| 完成条件 | 自检 |
| --- | --- |
| `tests/test_deviation.py` 增加 `set_parameter("rc","tol_c",-0.3)` 与 `hidden` 故障注入两条用例，开关关闭/打开各断言一次 | ✅ `test_the_session_view_hides_set_parameter_writes_unless_revealed`、`test_the_session_view_hides_hidden_fault_writes_unless_revealed`（关=0.0、开=−0.3；`uv run pytest tests/test_deviation.py -q` 18 passed） |
| progress-log 补一句 `hidden` 事件的核对结论 | ✅ `docs/progress-log/1674-1676-web-canvas-reveal-units.md` 自检列补：引擎事件的 `value` 只留在运行记录（`ENGINE_SIDE_KEYS`），WS 流实时与 backlog 都经 `project()` 只带 `kind`/`source`，无需再遮蔽 |
