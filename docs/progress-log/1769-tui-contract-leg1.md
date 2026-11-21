## TUI 契约第三端（腿 1）：快检 fixtures 对 contracts 模型校验；揪出并修掉两处第三端漂移（Issue 1769）

| 完成条件 | 自检 |
| --- | --- |
| `tests/test_contract_models.py` 新增 fixture 校验：unit_smu/unit_scope_rc 的 session→`SessionInfo`、instruments→`InstrumentInfo`、clock→`ClockInfo`、levels→`NetLevels`、readings→`InstrumentReadings`（无读数类别 None 跳过）、catalog→`ComponentTypeInfo` 逐个校验；DTO 必填字段改名→快检红 | ✅ 通过；instruments 是 dict（名字→信息）两种形状适配 |
| unit_catalog.json 对真实端点重生成：旧 fixture 缺契约的 `defaults`/`units` 两字段（校验当场抓住），且含已归 internal 的组件（catalog API 已不含）——旧 fixture 一直在超额撒谎 | ✅ TestClient + 真 `create_app`/`SessionManager` 重 dump（**必须先 `import simubench.library`**）；108 条 = 全部组件类型（含 internal）+ defaults/units |
| **漂移修复一**：`/api/catalog` 曾过滤 internal 组件——TUI/Web 的元件卡片从 catalog 读端口单位，internal 分家后它们被断了粮（TUI Load 盒丢 `kΩ`；诚实 fixture 让 t2 的 SMU 测试当场红） | ✅ `benchfiles.catalog()` 收录全部组件类型（placement 面板自己按 category 过滤——Web 的 LibraryPanel 本就 `category === "part"` 过滤，无污染）；`M.Catalog` docstring 同步 |
| **漂移修复二**：TUI 两处读 catalog 的**已消失字段** `params`（契约早已改为 `units` 映射，Web 早已迁移，只有 TUI 掉队）：`cards._units_of`、`instrument._param_unit` 改读 `units` | ✅ `test_smu_screen_renders_identity_columns_and_reason` 复绿（Load 盒 `1.0000 kΩ` 回来）；t2+t3+units+contract 195 过 |
| 腿 2（TUI 是否类型化、engine 永不 import 纪律是否放宽）留 issue 裁决 | ✅ 本 PR 只落腿 1；docstring 的「reuses contracts / engine is never imported」矛盾待腿 2 定案后改口 |
