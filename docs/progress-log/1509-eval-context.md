## 求值上下文显式化：`EvalContext` 代替会话上散着的 tuned / at_instant 两个属性（Issue 1509）

| 完成条件 | 自检 |
| --- | --- |
| 一个冻结的上下文类型 | ✅ `engine/evalcontext.py`：`EvalContext(tune_hz, at_ns)`，frozen + slots；`Session.context` 返回当前的，`Session.evaluating(**changes)` 是改它的唯一入口（可重入，退出恢复），`tuned` / `at_instant` 是它的具名写法；`Session.tune_hz` / `instant_ns` 留作只读属性，组件与插件的接口不变（D60），旧的 `session.tune_hz = …` 式写入本来只发生在会话内部 |
| 键由整个上下文得出 | ✅ `cached_output` 与 `cached_frames` 的键尾直接是 `self._context`（#1486 的 `_evaluation_inputs()` 随之删除）；两个缓存共用同一个类型，所以新增字段自动进两个键 |
| 新增字段会被逼着面对键 | ✅ `tests/test_stream.py`：`OTHER_VALUE` 要为 `EvalContext` 的每个字段给一个非默认样例，少一个就红（`test_every_field_of_the_evaluation_context_has_a_sample_here`）；逐字段参数化的 `test_both_caches_key_on_every_field_of_the_context` 断言改变该字段后帧和信号都重算、回到原上下文后各自命中。变异检查：两个键去掉上下文，4 例变红（逐字段 2 例 + #1486 的 2 例） |
| 取舍 | ✅ 不取「`Component.output(port, ctx)` 显式参数」：要改全部组件与插件接口（D60 公共口），而键既然由整个上下文对象得出，它的安全收益很小；动态作用域保留，但内容是冻结值、变更只有一个入口。理由写进 D67 |
| 行为不变 | ✅ 快速套件全绿，金测零变化 |
| 文档 | ✅ decisions.md D67 那条改写；signal-chain.md 帧缓存键一句；codebase-map 新增 evalcontext 一行 |
| 对原验收的修订（#1532） | ✅ 原验收第一条要求「块代码里不再出现从会话读 `tune_hz`/求值时刻的写法，grep 级守卫锁定」，前提是去掉动态作用域；实现选了保留（D67 备选二），所以该条改为「动态作用域保留，键由整个上下文得出，由 `test_stream` 的字段枚举测试锁定」。块经只读属性读这两个值（`specan.py` 的 `tune_hz`、`optical.py` 的 `instant_ns`）是键覆盖了的读法，不加守卫；理由写进 D67 |
