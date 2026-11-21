## SRQ 宣告收进总线：锁存变化推给全部传输，HiSLIP 每会话送达；总线控制事实一个 kind（Issue 1762，D96）

| 完成条件 | 自检 |
| --- | --- |
| 规范核对：IVI-6.1 6.13（官方 PDF，ivifoundation.org）义务是每连接的——server 请求服务就发 AsyncServiceRequest，rqs 由客户端 AsyncStatusQuery 清除启用下一条；HiSLIP「只通知引发会话」是漂移 | ✅ 6.13 原文在 /tmp（"The server requests service by sending the AsyncServiceRequest message... the client must do an AsyncStatusQuery to clear the rqs bit and enable additional AsyncServiceRequest messages"）；无「仅发起会话」限定 |
| `bus.py`：`InstrumentBus.srq_listeners`（WeakMethod 名册）+ `add_srq_listener`（幂等、记注册时的循环）+ `observe`/`serial_poll` 在锁存或理由变化时 `announce_srq`（call_soon_threadsafe 渡河到服务器循环） | ✅ `tests/test_bus.py` 原样过；`_watch`（引擎线程）与传输（循环内）两条路径都可达 |
| VXI-11：`_poll_srq` 变总线宣告员（去自身的 observe），`_create_link` 注册；write/trigger 后的两处直呼删除 | ✅ 既有 `test_srq_is_delivered_on_the_interrupt_channel` 原样过 |
| HiSLIP：`announce_srq(instrument)` 对该仪表**每个会话** `poll_srq`，建会话处注册；三处直呼删除；TLS 换挡两处保留（传输缓冲的重投递） | ✅ 既有 `test_service_request_when_rqs_rises`、TLS 测试原样过 |
| 跨传输送达两条新测试（旧代码下都红，patch 还原实测） | ✅ `test_an_srq_raised_over_hislip_reaches_the_vxi11_interrupt_channel`（复用既有 SRQ rig 形状 + 双服务器 rig 先例）、`test_an_srq_raised_over_vxi11_reaches_a_quiet_hislip_session`；旧代码 `git checkout` 后两测均 FAILED，恢复后双绿 |
| 事件 kind 归一：HiSLIP 的 REN/GTL/LLO 事实发 `"bus_control"`（与 VXI-11/GPIB/USB 同嘴）；engine/events kind 表删 `"hislip"`；schema 与生成 TS 重导出 | ✅ `schemas/events.schema.json`/`openapi.json` 与 `web/src/api/generated/{events,openapi}.ts` 各 -hislip 一行；无消费者按 kind 分叉（grep 证实） |
| 四协议端到端测试原样存活 | ✅ `uv run pytest tests/test_vxi11.py tests/test_hislip.py tests/test_bus.py tests/test_pyvisa_buses.py tests/test_usbtmc.py -q` 174 过 |
