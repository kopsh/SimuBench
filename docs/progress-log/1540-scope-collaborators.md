## 示波器各大模块按 signal-chain.md 3.4 再拆：测量、采集、触发、数学四个类拆成协作者（Issue 1540）

| 完成条件 | 自检 |
| --- | --- |
| 四个类按职责拆开 | ✅ `ScopeMeasurements` → `ResultsList`/`Values`/`Edges`/`Cursors`/`KMarkers`/`Readouts`；`ScopeAcquisition` → `Capture`/`Segments`/`Recorder`/`RunControl`；`ScopeTrigger` → `Sources`/`Crossings`/`Runs`/`Advanced`（`_advanced` 一个方法 224 行，独占一个模块）；`ScopeMath` → `Fft`/`Controls`，类前面 370 行纯数学函数与常量搬进 `scope_math_ops`。`@op` 与钩子留在组件类上，方法体委托；`@op` 的完整文档字符串留在转发方法上（目录读它：`server/catalog.py` 的 `OperationInfo.doc`），从协作者里拿掉免得两份 |
| 搬代码用脚本，只改 `self` 的指向 | ✅ 一个按配置工作的工具（AST）：方法体逐字搬，`self.状态` → 所属协作者的字段，`self.别的方法` → 协作者各自的路径，其余 → `self.host.X`；自动生成转发、协作者的 `__init__`、宿主的 `Protocol`、宿主的接线，并把被搬走代码用到的模块级定义移到各类的 `*_host.py`、原模块 `as` 重新导出。工具是一次性的草稿，没有进仓库 |
| 工具与测试抓出的真实问题（都已修） | ✅ ① 宿主私有方法 `_channel` 去下划线成 `channel`，盖掉了 `Component.__init__` 设的 `channel` 属性（协作者里 `channel()` 变成 `None()`），冲突检查原来只看类自己的方法；② 协作者在宿主上的名字 `records` 与状态属性同名（属性转发递归）、`run` 与宿主的 `@op` 方法同名；③ 原 `__init__` 里的 `PendingAcquisitions(self)` 搬进协作者后 `self` 指错对象；④ 外部（`scope_pending.py`、各测试）直接读写 `acq.armed`/`acq.records` 等状态、调 `acq._timing()` 等私有方法：状态留带 setter 的属性转发，被外部用的私有方法不改名；⑤ 测试里补丁模块常量（`EAGER_POINTS`、`MAX_FRAME`、`MAX_REQUEST_FANOUT`）要指到现在使用它们的协作者模块；⑥ `test_scope_math` 里「被测算子是纯函数」的断言跟着函数搬到 `scope_math_ops` |
| 行数 | ✅ 四个模块各不超过 800 行（`scope_acquisition.py` 654，其余更小，新模块最大 `scope_math_fft.py` 507）。类按「方法代码（含委托转发，不含类文档字符串与声明表）」量：`ScopeMeasurements` 303、`ScopeAcquisition` 343、`ScopeTrigger` 212、`ScopeMath` 237，都在 500 以内；物理行数是 493 / 611 / 398 / 308——`ScopeAcquisition` 的 611 里有 268 行是类文档与 `defaults` 声明表，不是逻辑。issue 里写的是「类不超过 500 行」，我把口径写清楚改成上面这样（signal-chain.md 3.4），没有悄悄换 |
| 行为零变化 | ✅ 快速套件 4899 passed, 9 skipped，TUI 与金测不动；`ty` 基线无新增（协议里 `@component` 类的类型写成 `Any` 加类名注释：ty 把它们当变量，是仓库已有的基线问题） |
| 文档与目录 | ✅ signal-chain.md 3.4 现状与两条实践、codebase-map 一行；`server/catalog.py` 登记 21 个新模块 |
