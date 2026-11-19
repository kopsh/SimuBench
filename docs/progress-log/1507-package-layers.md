## 包分层单向、无环：消除 8 组包间循环依赖，`check_layers.py` 守护（Issue 1507）

| 完成条件 | 自检 |
| --- | --- |
| 环清零 | ✅ 原有 8 组包间环（models↔engine、models↔library、engine↔library、engine↔plugins、engine↔api、library↔api、assistant↔server、ops↔server）按 D87 的层把模块挪到所在层：`library/{migrate,macros,same_as,panels,categories}` → `models/`、`models/{api,tools}` → `contracts/`、`plugins.py` → `engine/plugins.py`、`engine/{replay,verdicts}.py` → 顶层、`assistant/` → `server/assistant/`、`ops.py` → `server/ops.py`、用例运行从 `library/coverage.py` 拆到 `cases.py`；引擎不再 import 组件库（`simubench.library` 被 import 时注册内置组件），`validate_bench` 的未知型号候选取自传入的库。import 由 AST 机械改写（含相对 import、字符串里的模块路径、文档路径），`replay.py` 的延迟 import 回到模块顶层 |
| 检查会失败 | ✅ `scripts/check_layers.py`（`check.sh` 与 CI python 任务）：向上 import、包间环（同层也算）、低层与 `server/services` import `fastapi`/`starlette`/`mcp`、未登记的顶层包，各报一行；函数内 import 计入、`TYPE_CHECKING` 不计。在 `origin/main`（7f656da）的原树上跑报 34 条（3 个未登记包 + 全部向上 import + 两个环），本分支 0 条 |
| 每条规则有单测 | ✅ `tests/test_check_layers.py` 6 例：函数内向上 import、TYPE_CHECKING 不计、同层环、service 引 fastapi（routes 引则不报）、未登记包、根包 `__version__` 属基础层 |
| 行为不变 | ✅ 快速套件与基线同数（4843 passed, 9 skipped）；schemas 零变化（`simubench schemas` 后 `git diff schemas` 空），所以 Web 生成类型不变；`ty` 基线只改 16 行文件路径（随文件搬移），无新增诊断 |
| 文档 | ✅ decisions.md 新增 D87，D57/D80 等条目里的路径随搬移更新；architecture.md 第 0 节加分层一段；codebase-map（分层段、cases 行与各搬移路径）、dev-workflow（检查命令）、web-ui skill 路径同步 |
