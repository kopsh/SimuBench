## Session 职责拆分：缓存、求解、连线解析各有住址，介质专用逻辑移出核心（Issue 1508）

| 完成条件 | 自检 |
| --- | --- |
| 切法按依赖图实测定 | ✅ 先量：生产代码里没有任何外部模块碰会话的私有缓存/备忘成员，测试里只有 `_frame_cache`/`_frame_cached`（4 处）、`session_module.<常量>`（3 处）、`_signal_version` 等少数几处——缓存和求解能干净切出；时钟（`now_ns`、`clock_mode`、`time_scale`）、事件（`history`、`stats`）、仪表读写是被一百多处直接读的公共属性或动作协议本身，拆出去只多一层转发，不拆（理由写进 D60） |
| 缓存 | ✅ `engine/evalcache.py` 的 `EvalCache`：输出信号缓存、采样流帧缓存、一次读取的采样扇出记账，整段原样搬，键由会话传入版本与 `EvalContext`，不持有会话；`Session.cached_output/cached_frames/reserve_frames` 转发 |
| 求解 | ✅ `engine/solver.py` 的 `Solver`：电路与解的备忘、模式迭代、`unsettled` 判定与那条 `solver` 事件；只经 `SolveHost` 协议（`stats`、`signal_version`、`electrical_version`、`all_components`、`node_of`、`revise`、`emit`）读会话；`Session.solve/circuit/unsettled` 转发。模式循环里的版本递增走新的 `revise()`（不带 `invalidate()` 的 sync，那是在 solve 里面） |
| 介质专用逻辑出核心 | ✅ `nets.feed(members, component_at, comp, medium)`：一个网上某介质的唯一输出，与光无关；`Session.net_feed(comp, port, medium)` 是入口，`optical_feed/optical_input/optical_carrier` 只留 InstrumentHost 协议（D78）要求的薄名字，下一种非电路介质传自己的名字 |
| 数字 | ✅ `session.py` 993 → 867 行（−13%）；`Session` 方法与属性数 63 → 68：搬走的是实现，留下的转发与新增的 `context/evaluating/revise/net_feed` 等公共口让名字数不降——写在这里，不拿它充数 |
| 组件零改动、行为不变 | ✅ 组件代码零改动；快速套件 4858 passed, 9 skipped（基线 4843 + 本系列新增用例），金测零变化；测试里的私有访问改指新住址（`session.cache._frames`、`evalcache.MAX_REQUEST_FANOUT`），断言不动 |
| 隐私守卫 | ✅ `tests/test_component_privacy.py` 的扫描扩到 `session.cache._*` / `session.solver._*`，守卫自测加一例 |
| 基线 | ✅ `ty` 基线里 `session.py` 名下的三条求解相关诊断随代码移到 `solver.py`（路径改名，条目数不增） |
| 文档 | ✅ decisions.md D60 补「会话的协作者」一条（含不拆什么与理由）；codebase-map 加一行 |
