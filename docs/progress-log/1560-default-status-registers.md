## 默认 STATus 寄存器族：自定义仪表的 OPERation / QUEStionable / PRESet（Issue 1560）

| 完成条件 | 自检 |
| --- | --- |
| 默认寄存器读写 | ✅ `tests/test_builtin_commands.py`：enable、PTR、NTR 读写，事件与条件读 0，两个寄存器各自独立，`*CLS`、`*RST` 不改，`PRESet` 恢复，越界 -222、缺参数 -109、方向错 -113 |
| 型号自己有命令时不混用 | ✅ `tests/test_builtin_commands.py`：写了 `STATus:OPERation:CONDition` 的定义，`ENABle` 回 -113，另一个寄存器仍有默认 |
| 受 `builtins` 名单限制 | ✅ `tests/test_builtin_commands.py` |
| 路由与静态判定一致 | ✅ `tests/test_command_equivalence.py` 的语料加了 12 条 STATus 报文，每个内置型号一致 |
| 真实型号不回常量 | ✅ 带 `reference` 的定义得不到默认（`tests/test_builtin_commands.py`），覆盖表不动；各型号按手册建模另开 issue：34465A #1569、8588A #1570、53220A/53230A #1571、B2901A/B2912B #1572 |
