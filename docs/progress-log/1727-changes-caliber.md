## 助手的定义变更报告改走分层口径（Issue 1727）

| 完成条件 | 自检 |
| --- | --- |
| 定义变更只出一个口径：`FileStores` 门面 definition 侧的 `changes` 改问 `DefinitionFiles.changes`（原 `_changes` 升为公共方法，分层对比 D59），assistant 的 turn 摘要（`_summarize`）与回滚 lost（`rollback_plan`）不再自己把原始数据喂给 `definition_changes`；bench 侧 `bench_changes` 不动 | ✅ `tests/test_filestores.py::test_definition_changes_come_from_the_layered_comparison_not_a_raw_one`（对 `extends` 父的副本改一个继承默认值：门面结果与 `DefinitionFiles.edit` 的工具口径逐项相等，读到的是 changed 而非 added——原始口径会把同一编辑说成整段 state 新增） |
| 等价性测试：一次带 extends/类别默认值的编辑，工具回复的 changes 与 turn 摘要 items 相同，旧代码下红 | ✅ `tests/test_assistant.py::test_a_definition_edit_summary_tells_the_tool_replys_story`（`copy_of` 真实型号 siglent-sdg2042x（reference 在链上，类别默认参与合并）后 `edit_definition` 覆盖继承默认频率：`[_change_text(c) for c in summary.changes] == reply["changes"]` 且逐项元组相等；把门面临时改回 `definition_changes(old, new)` 时本测试与上一条皆红，已还原提交态） |
| grep 守卫：`definition_changes` 的直接调用只剩 `DefinitionFiles` | ✅ `tests/test_filestores.py::test_definition_changes_is_called_directly_only_by_definitionfiles`（ast 扫描 `src/simubench/server/**/*.py`，直接调用方只许 definitionfiles.py） |
| 现有变更报告/回滚测试原样存活 | ✅ `uv run pytest tests/test_assistant.py tests/test_assistant_rollback.py tests/test_definition_changes.py tests/test_definition_history.py -q`（72 过）；#1729 的既有守卫（summary 缺 kind、service 无 kind 分派）照常通过 |
| `scripts/check.sh` 全绿（退出码 0） | ✅ 本分支改在共享代码，`select_tests.py` 选全量：Python 5453 过；单独步骤补跑 `check_tui_i18n`/`check_test_rules`/`check_layers`/`check_skills`/`check_progress_log`、schemas 无漂移、web 四项均过。唯一红是 `tests/test_select_tests.py::test_shared_code_runs_everything` 的 unit CPU 预算临界（本机 0.108–0.121 s > 0.1 s，全量跑完紧跟着跑时出现，静置后单跑即绿、origin/main 同样偶发）——已知存量 #1716 一族，本分支不修。CI 预算为 0.3 s，不受影响 |
| 不写新 D：definitionchanges 契约纠偏，docstring 点名唯一直接调用方 | ✅ `docs/decisions.md` 未追加新节；`server/definitionchanges.py` 模块 docstring 的契约句改为「`DefinitionFiles.changes` 是唯一直接调用方」 |
