## Session 再拆：时钟、事件流、仪表文件各有住址，仪表缝不再点名介质（Issue 1531）

| 完成条件 | 自检 |
| --- | --- |
| 拆出 Clock 与 EventBus | ✅ 两个都拆。`engine/clock.py` 的 `Clock`：`now_ns`、`mode`、`time_scale`、`idle_mode`、`replaying`、`confirm_advance`，时间动作 `advance`/`wait`/`seek`/`set_time_scale`/`follow_wall`/`calendar`，动作首尾的空闲记账 `begin_action`/`end_action`、链路延迟 `delay`、引擎自己的一步 `step`；只经 `ClockHost`（`sync`/`revise`/`events`/`faults`）读会话。`engine/eventbus.py` 的 `EventBus`：`emit`/`subscribe`/`instrument_event`、`history`、`stats`、`event_id`，运行记录挂在 `record` 出口上 |
| 顺带：仪表文件 | ✅ `engine/instrument_files.py` 的 `InstrumentFiles`（`Session.files`：`folder`/`snapshot`/`close`），原 `files_root`/`instrument_files`/`files_snapshot` 三个方法与临时目录状态 |
| 不留转发 | ✅ 调用方（服务、MCP、访问层、回放、组件、测试）改指 `session.clock.*` / `session.events.*` / `session.files.*`。只留两个名字直接交给时钟：`Session.now_ns`（只读属性，组件与测试替身到处读）与 `Session.advance`（pytest 插件 `bench_session` 的 SDK 动作，README 写着它） |
| 数字 | ✅ `Session` 的 `^    def ` 计数 70 → 54（#1508 前 63），`session.py` 906 → 709 行 |
| 介质通用入口 | ✅ `InstrumentHost` 的 `optical_input`/`optical_carrier` 删除，换成 `net_feed(comp, port, medium)`；`Component.input`/`input_carrier` 自己从 `net_feed` 取源再读输出或波长。`Session.optical_feed/optical_input/optical_carrier` 删除；能接线的介质名单移到 `nets.WIRED_MEDIA`。协议成员 17 → 13（时钟经 `HostClock`、事件经 `HostEvents` 两个窄协议）；`test_instrument_host.py` 加一例：协议成员里没有任何介质名 |
| 行为不变 | ✅ 快速套件全绿（数字见 PR），金测零变化；错误文案不变 |
| 隐私守卫 | ✅ `tests/test_component_privacy.py` 的协作者名单扩到 `clock`/`events`/`files`，守卫自测加两例（私有成员被点名，公共成员不被点名） |
| 文档 | ✅ D60 的「会话的协作者」改写（拆了什么、留了哪两个名字、仪表读写为何不拆）；D78 的协议成员清单；D63 连接模式里的 `Session.wait` → `Session.clock.wait`；architecture.md 光纤一句；codebase-map 加一行 |
