## 不再编造 SCPI 地址：address_virtual 空态、5025 常量一处、preview 哨兵显式化（Issue 1488）

| 完成条件 | 自检 |
| --- | --- |
| 地址是事实不是猜测 | ✅ `address_virtual` 无 SCPI 监听时为 `null`（契约改 Optional、生成 TS 随动）；原 `scpi_port or 5025 + index` 兜底从视图退役——`--no-scpi` 与 try 会话不再给 UI 与 agent 一个无人监听的地址去复制；Inspector 在无 SCPI 时显示「未开启 SCPI 服务」空态（#1536 补齐，此前只是整行隐藏）；首页的 `filter(Boolean)` 原样 |
| 5025 一处 | ✅ 默认端口只定义一次：`scpi_server.DEFAULT_SCPI_PORT = 5025`，CLI、manager、pyvisa 后端都引用它；视图层的 `5025 + index` 兜底随上一条消失（#1536 补齐，别名 `DEFAULT_SCPI_BASE_PORT` 已删） |
| socket 地址一处 | ✅ `scpi_server.scpi_socket_address(host, port)` 是 `TCPIP0::…::SOCKET` 的唯一生成点；views、projections、`ScpiListener.address` 与 pyvisa 后端都引用它（#1536 补齐） |
| preview 哨兵显式化 | ✅ `ServedSession(preview=True)` 标志取代 `served.id != "preview"` 字符串暗号（views 的 stale 守卫读标志）；preview 的会话 id 字面量只是展示名，不再承载语义 |
| 测试 | ✅ 新增 `test_without_scpi_the_virtual_address_is_none`（scpi_base_port=None 的会话全仪表 address_virtual 为 null）、Inspector 空态组件测试与 TUI 空态测试（#1536/#1537）；既有地址断言（test_link_served/test_security 的有 SCPI 侧）原样通过；schemas 与 web 生成类型已再生 |
| check.sh | ✅ 全绿 |
