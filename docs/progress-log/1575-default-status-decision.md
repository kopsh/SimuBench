## 默认 STATus 寄存器的设计偏离记入决策与测试（Issue 1575）

| 完成条件 | 自检 |
| --- | --- |
| `docs/decisions.md` 新增 D89：默认 STATus 族只给没有 `reference` 的定义、受 `builtins` 约束、默认寄存器不置状态字节位 3/7 | `grep -n 'STATus' docs/decisions.md` 命中 D89 |
| 新增 flow 测试：自定义仪表写使能、`*CLS`、读 `*STB?`，断言默认寄存器不使 OSB/QUES 置位 | `tests/test_builtin_commands.py::test_the_default_status_registers_never_set_the_status_byte_summary_bits` |
| `command-coverage` skill 补「带 reference 的型号 STATus 行保持 not_modeled（回 -113），不得标 accepted」 | `uv run python scripts/check_skills.py`；`scripts/check.sh` |
