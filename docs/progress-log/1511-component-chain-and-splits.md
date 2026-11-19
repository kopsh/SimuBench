## 仪表组件链的内部规范与大文件拆分：validate_definition 成包，specan 按组件类拆（Issue 1511）

| 完成条件 | 自检 |
| --- | --- |
| 规范成文 | ✅ `docs/signal-chain.md` 3.4「一台仪表的组件链怎么切」：一个块一件事；块之间只走端口与 D62 协议；一个模块装一段链、依赖方向叶子块 → 驱动块；何时再拆（模块 800 行、类 500 行）与不用混入类的理由；现状与欠账指向 #1524 |
| `validate_definition.py` 成包 | ✅ `engine/validate_definition/`：入口 `__init__`（`validate_definition_data`、`validate_definition`、再导出 `operations` 与测试用的 `_protocol_gaps`），按关注点分七个模块 `lookups`（底层查找）→ `params` → `parts` / `states` / `wires` / `cmds` / `panel`，低到高、无环（调用图用 AST 先算出再分）；最大的 `cmds.py` 466 行（原一个文件 1622 行）。逐字搬代码，相对 import 机械加深一层（含函数体内的延迟 import——一处 `scpi.values`，是测试抓出来的）；公共入口路径不变，外部 import 零改动 |
| `specan.py` 拆分 | ✅ 按组件类拆而不是混入类：`specan_frontend.py`（输入、本振、混频、中频滤波，469 行）、`specan_detect.py`（检波、视频滤波，162 行），`specan.py` 留迹线驱动块（1605 → 1014 行）；`__all__` 与类的再导出不变（`tests/test_specan_model_limits.py` 仍从 `specan` 导入）；新模块在 `server/catalog.py` 的 `GROUPS` 登记（组件目录的测试抓出来的）。`SpecanTrace` 一个类仍约 870 行：实测游标段用宿主 11 个成员、扫描段 19 个（一半是实例状态），拆它要先收状态，另开 #1524，没有为凑数写混入类 |
| 行为零变化 | ✅ 快速套件全绿，金测零变化；`ty` 基线只随文件路径改名（validate_definition 一条、specan 一条），条目数不增 |
| 文档与 skill | ✅ codebase-map（validate_definition 行、组件链切法一行）、instrument-authoring.md 与 signal-chain.md 里的旧路径；`check_skills.py` 通过 |
| 未做 | 示波器各模块（1200–1450 行）是否再拆：按 3.4 的规则它们该拆，但那要先读懂每个模块的内部耦合，与 #1524 同性质，没有在这个 PR 里碰 |
