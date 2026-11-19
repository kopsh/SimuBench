## SpecanTrace 按职责拆成协作者：先把实例状态收成 pydantic 模型（Issue 1524）

| 完成条件 | 自检 |
| --- | --- |
| 先收状态 | ✅ 11 个散落的实例属性按谁读写它们分成三簇，各是一个 pydantic 模型（`library/components/specan_state.py`）：`TraceHistory`（重启时刻、历史信号、已算的扫描缓存；带 `index`/`signal_at`/`restart_at`/`keep`，缓存满了重来）、`LinesCache`（前端谱线，按对象身份取键）、`HoldState`（最大/最小保持、单次扫描的冻结迹线、一次性警告）；顺带 `_Settings`/`_Plan` 两个 dataclass 换成 pydantic（`TraceSettings`、`SweepPlan`，冻结）。`LinesCache` 靠对象身份判断「同一次读取」，所以字段赋值不复制（测试钉住了） |
| 协作者经窄协议读宿主 | ✅ `Sweeper`（`specan_sweep.py`：前端谱线、解析求值、采样求值，313 行）与 `Markers`（`specan_markers.py`：游标与峰值搜索，186 行）只经 `TraceHost`（`specan_state.py` 的 `Protocol`，列出它们需要宿主的每一样东西）读 `SpecanTrace`；宿主的 8 个下划线访问器（`_st`、`_set`、`_input` …）为此改成公共名（`setting`、`set_setting`、`input_block` …，外部实测零使用）。`@op` 与钩子（`on_state`、`tune_marks`）留在组件类上、方法体委托给协作者 |
| 搬代码用脚本 | ✅ 方法体由 AST 脚本逐字搬，只改 `self` 的指向（状态 → 状态模型，已搬的方法 → 所属协作者，其余 → `self.host`）；手写的只有状态模型、协议、类壳与委托。脚本抓出两处我的映射错误：`_history_index` 被改成 `history.history_index`（实为 `index`，由 specan 的测试抓出），和协议里 `uid` 要写成只读属性（`ty` 抓出） |
| 行数 | ✅ `SpecanTrace` 类 875 → 430 行；`specan*.py` 最大 506 行（`specan.py`），都在 3.4 的限度内 |
| 行为零变化 | ✅ 快速套件 4899 passed, 9 skipped（其中本 PR 新增 9 例），`tui_boot` 层 147 passed；specan 全部测试与 N9042B 手测用例原样通过，金测零变化；`ty` 基线无新增（协议里的块访问器用 `Any` 加类名注释：`@component` 类 ty 当变量，不能做返回类型，这是仓库已有的基线问题，不为此增加基线条目） |
| 新状态的直接单测 | ✅ `tests/test_specan_state.py` 9 例：历史里某时刻的输入是哪个信号、重启清掉旧扫描、扫描缓存满了重来、两个历史不共享列表、重启不清冻结的单次扫描、谱线缓存保身份、设置的键随影响迹线的字段变化且冻结、`same_signal` |
| 文档 | ✅ signal-chain.md 3.4 加第 5 条「协作者的写法」（以迹线为样板）、第 6 条现状，architecture.md 里 `MAX_SWEEP_BINS` 的位置，codebase-map 加一行；`server/catalog.py` 登记三个新模块。示波器各模块（1200–1450 行）按同样规则拆，另开 #1540 |
