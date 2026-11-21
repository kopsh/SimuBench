## TUI 推进时钟遇「脚本已连接」409 显示服务端 message，而非原始 dict（Issue 1782）

| 完成条件 | 自检 |
| --- | --- |
| `_do_advance` 对 dict 型 `detail` 取 `detail["message"]` 再闪底栏，字符串 `detail`（普通 `RuntimeError` 的 409）保持原样，不再 `str(dict)` 露出 `{'message':…, 'clients':…, 'reason':…}` | ✅ `src/simubench/tui/app.py` `_do_advance` 一处改；`tests/test_tui_t3.py::test_a_409_advance_says_why` 对还原前代码实测红（断言 `'clients' not in` 命中 dict repr） |
| 回归测试用服务端真实形状：直接取 `_http_in_use(ClientsConnected(...)).detail`，断言底栏含 message 且不含 `clients` 键名 | ✅ 同一测试；`uv run pytest tests/test_tui_t3.py -q` 19 过 |
| TUI 层其余用例不受影响 | ✅ `uv run pytest tests/test_tui.py tests/test_tui_units.py -q` 144 过 |
