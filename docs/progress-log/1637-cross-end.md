## 面板 when 双端一份测试向量，类别注册表补读数键列（Issue 1637）

| 完成条件 | 自检 |
| --- | --- |
| 面板 `when` 的值比较语义由一份共享向量钉住，Python 与 vitest 各有消费测试 | ✅ `web/src/features/workbench/when-cases.json`（20 条值比较 + 10 条 when 求值用例）；`tests/test_tui_units.py::test_a_panel_when_holds_by_the_shared_vector` 跑 TUI 的 `_same`/`when_holds`，`model.test.ts` 的两条 it 跑 `sameValue`/`whenHolds`，双端跑同一份文件 |
| Web 的 `sameValue` 改齐 TUI 口径（裁决：以 TUI 语义为准），行为变化逐条裁决 | ✅ `model.ts` 的 `sameValue` 重写为 TUI `_same` 的形状：两侧都是数（只有 JSON 拼写 INF/-INF/NAN 算数）才按数比，否则按原值比。改齐的用例见 PR 正文；issue 正文里「TUI 把 "1e3"/"1000" 按数值相等」的说法与代码不符——`jsonsafe.as_number` 只转 INF/-INF/NAN 三个拼写，不解析一般数字串——裁决落在 TUI 的实际代码语义上，向量按此钉住（"1e3" vs 1000 → 不等） |
| 类别注册表行加「读数键」列，TUI 消费点从行里取 | ✅ `models/categories.py` 的 `Category.reading_keys`（smu 12 角色、lockin 11 角色，值即原字面量串）；`tui/cards.py`（smu_rows、_alerts）、`tui/rows.py`（smu_rows、reason_line）、`tui/reasons.py`（limited_report）改读行里的键。测试 fixture 补 `display.template`（`SMU_INFO`、`_workspace_smu`）——函数需要类别来查行，describe 本来就带它 |
| Web 消费点从行里取：读数键经 API 贯通，生成类型闭合 | ✅ `InstrumentInfo.reading_keys`（`contracts/api.py`），schema 由注册表行推导（`_reading_keys_schema`：每个角色一个必填属性、不得多），`server/views.py` 从 `categories.get(...)` 填充；`pnpm gen:api` 后 TS 类型是 23 个角色的闭合 Record，`SmuScreen.tsx`/`LockinScreen.tsx` 改读 `inst.reading_keys.<角色>` |
| 完整性测试 | ✅ `tests/test_categories.py` 三条：键名 `<block>.<name>` 且块名是该类别某个内置/验证定义真的声明的组件名；发布的 API schema 与注册表角色一一对应（additionalProperties false）；TUI 三个消费文件无 `core.`/`demod.`/`osc.` 字面量（AST 守卫）。`tests/test_bench_api.py::test_readings_are_passive` 加一段：lockin 行声明的每个角色键都在实读载荷里（值角色在 `values`、投影在 `traces`） |
| Web 侧守卫 | ✅ `web/eslint.config.js` 对两个屏幕文件加 `no-restricted-syntax`：禁止 `core.`/`demod.`/`osc.` 开头的字符串字面量；漏角色由闭合类型编译期报错 |
| 现有测试原样存活 | ✅ 快速层全绿；唯一改动的既有断言是 `test_tui_units.py` 的两处 fixture（补 `display.template` 字段，喂新数据需求，断言未动） |
