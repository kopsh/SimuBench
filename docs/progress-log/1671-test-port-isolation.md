## 测试不再占用宿主机 4880：本机开着 SimuBench 也能跑 check.sh（Issue 1671）

| 完成条件 | 自检 |
| --- | --- |
| `tests/test_security.py` 的 `test_without_a_token_by_default_but_still_loopback_only` 构造 `SessionManager` 时补齐 `hislip_port=0, vxi11_port=0, portmapper_port=0, link_port=0` | ✅ 占用 4880 后单跑该用例：修复前 `KeyError: 'id'`（`POST /api/sessions` 返回 409「HiSLIP 端口被占用」），修复后通过；`tests/test_security.py` 全过 |
| `tests/test_server.py` 的 `test_try_session_binds_no_ports_and_writes_no_record` 同样补齐四个端口参数 | ✅ `tests/test_server.py` 全过（该用例走 try 会话，本就不开端口，补齐后与同文件其它用例一致） |
| `scripts/check_test_rules.py` 新增端口隔离规则：测试里的 `SessionManager` 若未显式给 `hislip_port`，就必须 `scpi_base_port=None`（完全不开套接字），否则报错并指出行号 | ✅ 对修复前的三处源码（`test_security.py:200`、`test_server.py:491`、`test_port_in_use.py:41`）均报错，修复后零告警；`uv run python scripts/check_test_rules.py` 通过 |
| 无其它环境依赖：占用 127.0.0.1:4880 时 `tests/test_security.py tests/test_server.py tests/test_port_in_use.py` 全过 | ✅ 占用 4880 下 70 passed |
| `scripts/check.sh` 全绿 | ✅ ruff、ty、pytest 5375 passed、tui i18n、test rules、layers、skills、progress-log、schemas、web（`check:api`/typecheck/lint/405 tests）全过；本沙箱 3 核下 pytest 261 s 属已记录的平台预算问题（#1668 → #1680），与本修复无关 |
