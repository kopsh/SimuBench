## 文件写事务一条缝：四份事务体与私有成员借用收进 `BenchFiles.write` 一个原语（Issue 1631）

| 完成条件 | 自检 |
| --- | --- |
| `DefinitionFiles` 不再 import `BenchFiles` 私有成员 | ✅ 守卫测试 `tests/test_definition_history.py::test_definition_files_go_through_the_write_transaction_not_its_privates`（ast 扫描：benchfiles 的下划线导入与 `files` 上的私有属性读取均为空），`test_the_private_borrow_guard_is_live` 钉住扫描本身活着 |
| 四份事务体消失；乐观并发习语 6 处收敛到一处 | ✅ bench `save`/`copy`/`create_hello`、定义 `save`、`save_layout` 全走 `BenchFiles.write`；`require_unchanged` 一处被原语锁内与 6 个写前快检点（bench/定义 `edit`、structure/identity 保存、模板另存、`remove_created`）调用；`grep -n "_lock\|os.replace\|\.tmp" src/simubench/server/definitionfiles.py src/simubench/server/mytemplates.py` 无事务残迹 |
| 现有文件域测试原样存活 | ✅ 保存/并发冲突/回滚/事件播报/外部改动版本链接/布局不记版本全过：`tests/test_bench_roundtrip.py`、`test_definition_history.py`、`test_hello_bench.py`、`test_my_templates.py`、`test_definition_structure.py`、`test_security.py`（#1457/#1501 两域回滚）、`test_server.py`（#1484 播报）、`test_mcp.py`、`test_template_draft.py`、`test_services.py` |
| `save_layout` 裁决写清 | ✅ 不补历史、不播报，在原语参数上明示豁免（`record=False, announce=False, overwrite=True`），理由在 D90 与 `save_layout` 的 docstring |
| 新 D 条目（D90）与 progress-log 新格式 | ✅ `docs/decisions.md` D90（文件写事务一条缝的落点）；本文件即新格式条目 |
