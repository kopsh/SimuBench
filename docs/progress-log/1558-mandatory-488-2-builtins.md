## IEEE 488.2 强制公共命令对所有型号都应答（Issue 1558）

| 完成条件 | 自检 |
|---|---|
| `builtins` 列表不能去掉 `*IDN *RST *CLS *OPC *WAI *ESR *ESE *SRE *STB *TST` | `tests/test_builtin_commands.py::test_the_mandatory_488_2_commands_answer_whatever_builtins_lists` |
| 可选内置命令（`*TRG`、`:SYSTem:*`）仍按 `builtins` 限定 | `test_a_builtin_the_definition_leaves_out_is_refused_as_excluded`、`tests/test_siglent_sds.py` |
| D45、real-models.md、字段说明同步 | `docs/decisions.md` D45、`docs/real-models.md` |
