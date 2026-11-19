## SCPI 端口与 socket 地址各只有一处定义（Issue 1536）

| 完成条件 | 自检 |
| --- | --- |
| 默认端口一处 | ✅ `grep -rn '\b5025\b' src/simubench --include=*.py` 只剩常量 `scpi_server.DEFAULT_SCPI_PORT` 与两处注释/docstring；`cli`、`service`、`server/manager`、`access/pyvisa_backend` 全部引用该常量，`DEFAULT_SCPI_BASE_PORT` 别名删除 |
| socket 地址一处 | ✅ `scpi_socket_address(host, port)` 是 `TCPIP0::…::SOCKET` 的唯一生成点；`views.describe_instrument`、`projections.session_summary`、`ScpiListener.address` 与 pyvisa 后端的地址表都调用它 |
| Inspector 空态 | ✅ `address_virtual` 为 `null` 时显示 i18n 文案「未开启 SCPI 服务」/“SCPI service is off”（`workbench.noScpiService`），不再静默隐藏整行 |
| 测试 | ✅ `Inspector.test.tsx` 新增空态用例；`tests/test_tui_t2.py` 覆盖 TUI 的同一下游（#1537） |
| progress-log | ✅ `docs/progress-log/1488-address-virtual.md` 按当前代码改写，不再声称 Inspector 只随值显隐 |
| check.sh | ✅ 全绿 |
