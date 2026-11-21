## SRQ 宣告员名册随服务器停止/失效清理；D96 的每会话与串行轮询再武装补测试（Issue 1780）

| 完成条件 | 自检 |
| --- | --- |
| 名册剔除死 WeakMethod 与已关闭事件循环的项：`InstrumentBus._live_srq_listeners()` 在注册、宣告、摘除三条路径上就地清理；锁保护名册（服务器在各自循环线程注册，引擎线程宣告） | ✅ `tests/test_bus.py` 原样过；`uv run pytest tests/test_hislip.py tests/test_vxi11.py -q` 全绿 |
| 服务器停止即摘除自己的宣告员：`HislipServer.stop()` 调 `remove_srq_listener(..., self.announce_srq)`，`Vxi11Server.stop()` 调 `remove_srq_listener(..., self._poll_srq)`，按 `_subaddresses` 的仪表逐一处理 | ✅ `test_a_stopped_server_leaves_no_announcer_on_the_bus`、`test_a_stopped_vxi11_server_leaves_no_announcer_on_the_bus`：反复创建/连接/停止 3 次后 `len(bus.srq_listeners)` 回到初值；旧代码下两测均 `assert 1 == 0` 红 |
| 同仪表两个 HiSLIP 会话：一个写入引发 SRQ，静默的另一个收到 AsyncServiceRequest（IVI-6.1 6.13 每连接义务） | ✅ `test_every_session_of_an_instrument_hears_a_service_request` |
| `serial_poll` 轮走 RQS 后再次置位：已武装的接收者再次收到宣告，且清除前不重复宣告 | ✅ `test_a_serial_poll_arms_the_next_service_request`：首条 AsyncServiceRequest 后 0.2 s 内无第二条，`status_query()` 取走 RQS 后 `*CLS;*ESE 1;*SRE 32;*OPC` 再宣告一次 |
| 文档同步 | ✅ D96「影响」补一句名册清理；本条目 |
