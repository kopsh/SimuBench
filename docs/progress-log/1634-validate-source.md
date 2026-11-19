## validate 补 source：draft.yaml 哨兵退役，extends 查找与引擎一致（Issue 1634）

| 完成条件 | 自检 |
| --- | --- |
| `DefinitionFiles.validate` 接 source：已保存文件用真实路径，未保存内容（草稿、MCP 裸 data）用「将要落地的路径」，哨兵只在这个语义下存在；`expanded`/`validate`/`structure` 三入口共用一套 source 语义 | ✅ `server/definitionfiles.py` 的 `validate(data, source=None)` + `_would_land`（数据带可用 id 取 `instruments/<id>.yaml`，无 id 才落共享的 `instruments/draft.yaml`）；`_document` 传 `self.source(def_id)`，与 `structure` 同源；`expanded` 本就走真实路径（`lib.path`），未动 |
| 内置定义经 validate 与经引擎两条路的 extends 解析等价（测试钉住，至少一个内置定义有 extends） | ✅ `tests/test_definition_layers.py::test_a_built_in_definition_layers_alike_through_validate_and_the_engine`：工作区放一份合法的父 id 同名文件（`keysight-uxr1104b`），validate（`defs.read("keysight-uxr0334b")`）与引擎（`DefinitionLibrary([工作区]).get`）都从内置文件自己的目录解析父、两条路都 ok；同一份数据按将要落地路径校验则命中间名父并报 E-DEF-COMMAND-OVERRIDE——钉死「靠运气通过」 |
| 坏 extends 的诊断指向真实文件路径，幻影 draft.yaml 消失（用例钉住） | ✅ `test_a_broken_extends_diagnostic_names_the_definition_s_own_file`：工作区定义的诊断指 `instruments/lonely.yaml` 且不含 draft.yaml（D76）；MCP 缝 `tests/test_mcp.py::test_validate_definition_checks_a_saved_definition_where_it_lives` 同钉 id 路（同名父不漏进内置子定义）与裸 data 路（诊断指将要落地的路径） |
| 未保存内容的落点语义有钉：有 id 指将要落地的 `<id>.yaml`，无 id 仍指 `instruments/draft.yaml` | ✅ `test_an_unsaved_draft_is_checked_where_it_would_land`：有 id 的坏 extends 诊断指 `instruments/orphan.yaml`；无 id 的指 `instruments/draft.yaml` |
| 现有校验测试原样存活 | ✅ `tests/test_definition_history.py`、`test_definition_structure.py`、`test_my_templates.py`、`test_mcp.py` 原样全过；`scripts/check.sh` 全绿（`ty_baseline.txt` 顺带缩一行：MCP 入参收窄后 `read` 的 arg-type 诊断消失） |
| 本 issue 不写新 decisions 条目 | ✅ `docs/decisions.md` 未动（语义收口，够不上「影响超出单功能」） |
