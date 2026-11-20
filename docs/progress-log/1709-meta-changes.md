## bench 元数据变更（描述/名称）计入变更与回滚，不再对用户写下的改动失明（Issue 1709）

| 完成条件 | 自检 |
| --- | --- |
| `bench_changes` 报告 description 变更（kind=bench, target=description, old/new），name 原有行为不变；布局位置继续排除（docstring 同步改写，说明设计意图变化） | ✅ `server/benchchanges.py` |
| 改描述的轮：turn.files 含该 bench、rollback_plan 列出 restore、回滚后描述还原 | ✅ `tests/test_assistant_rollback.py::test_a_meta_edit_lists_in_files_and_rollback_restores_it`（失败先行：旧代码 `turn.files == []`；回滚后描述恢复 Hello World 原文） |
| 既有测试不受影响 | ✅ `tests/test_assistant_rollback.py` + `tests/test_assistant.py` 34 过 |
| 背景 | ✅ 复杂 E2E 实测：set_meta 改描述 sha 确实变了但 files=[]，回滚覆盖不了「用户明确写下的改动」；原排除决定是为变更报告降噪，副作用是回滚盲区，现改为如实列出（definition_changes 早已报告 description，本就对齐） |
