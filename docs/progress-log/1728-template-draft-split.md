## 模板起草半区分家：Drafting 钩子搬进 *_draft.py（Issue 1728）

| 完成条件 | 自检 |
| --- | --- |
| 普查：有起草半区的模板全部照切 | ✅ `grep -ln 'DRAFTING' src/simubench/library/templates/` 只中 `oscilloscope.py`、`signal_source.py`，各拆一个 `_draft.py`，没有第三个 |
| 助记词与钩子搬进 `<template>_draft.py`，与机制文件 `templates/draft.py` 同居 | ✅ `oscilloscope_draft.py`（MNEMONICS 30 条 + base/basics/enable/refine/resemble，411 行）、`signal_source_draft.py`（MNEMONICS 10 条 + base/basics/enable/refine，192 行）；`Template` 注册面不动：`TEMPLATE = register(...)` 留在模板文件原位置，`drafting=DRAFTING` 由 `_draft` 模块喂入 |
| 逐字搬、零行为变化 | ✅ 搬动代码与原文逐字节相同（diff 验证），唯一差异是 oscilloscope 的 `_enable` 加一行晚绑定 `from .oscilloscope import FALLING_WORDS, RISING_WORDS`——这两个公开名留在 build 半区（`_timebase_trigger` 也在用），晚绑定与 `_basics`/`_refine` 现有钩子风格一致，先 import 哪个模块都不成环；signal_source 的 `_any` 改从 `oscilloscope_draft` import（oscilloscope 不再导出它）；消费面 `templates/draft.py`、`server/services/new_definition.py`、`mcp_tools/definitions.py` 一行未动 |
| 起草测试与金测原样存活 | ✅ `test_template_draft` / `test_templates` / `test_coverage_draft` / `test_my_templates` / `test_mcp` / `test_definition_authoring` / `test_signal_source` / `acceptance/test_lfcal_agent_scenario` 全过，金测零变化；`scripts/check.sh` 其余全绿，唯一失败为已知存量 `test_select_tests.py::test_shared_code_runs_everything` 的 0.1 s CPU 预算抖动（0.108–0.125 s，#1716，本机负载所致；CI 预算 ×3；与本次改动无关——该测试只跑 `scripts/select_tests.py`），本 PR 不修 |
| oscilloscope 主文件回 ~1300 行 | ✅ 1730 → 1328（新 `oscilloscope_draft.py` 411 行）；signal_source.py 682 → 497（新 192 行） |
| 新文件不 import 模板模块的私有成员 | ✅ 守卫测试 `test_templates.py::test_a_draft_module_imports_no_private_name_from_the_template_modules`（AST 扫 `ImportFrom`）：两个 `_draft` 模块对模板模块只读公开名 `RISING_WORDS`/`FALLING_WORDS` |
| ty 基线不增 | ✅ `scripts/check_ty.py`：463 diagnostics / 206 keys，none new；两条旧基线行指向的诊断都在 build 半区，路径无需改名 |
| 不写 D（纯拆分，无跨特性决定） | ✅ `docs/decisions.md` 未动 |
