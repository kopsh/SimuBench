## scpi 包语法裁决三处收口：short_form 一处、SI 进位循环一处、块语法对齐（Issue 1766）

| 完成条件 | 自检 |
| --- | --- |
| **short_form 一处**：`parse.Pattern.compile` 的行内推导改调 `values.short_form`（#1074「带数字的词保留数字」的裁决注释随迁到 values 侧） | ✅ `test_command_equivalence`+`test_command_runner`+`test_units_scpi`+`test_builtin_commands` 1057 过；parse→values 单向 import，无环 |
| **SI 进位循环一处**：`_exp3_with_carry(v, mantissa_of, cap=)` 统一三个 format 函数的「999.5 进位到下一前缀」——mantissa_of 参数化三种拼写（si 的 significant-f 格、engineering 的 %.6g、digits 的 whole/decimals 格），cap 承载 si 的 ±12 阶梯；**符号随 mantissa 走（engineering 的 `-3.52334E+12`），digits 的 lead 独立（mantissa 取绝对值）** | ✅ **数值等价对照 sweep：78k+ 组（±13 位量级随机 8k 值 × 参数矩阵）零失配**——sweep 还逮住统一初版的两处符号破坏（engineering 丢负号、digits 双负号），逐个修掉；`test_units_scpi` 等原样过 |
| **块语法对齐**：`parse.block_kind` 五态裁决（definite/indefinite/number/none/wait）成为 `split_units` 与 `MessageReader` 的共同判定——`#0`（indefinite）的载荷不再污染 socket 分帧（引号/`#` 在载荷内不再触发；对齐前 reader 把 `#0` 当普通字符，载荷可破坏分帧） | ✅ `tests/test_server.py::test_scpi_message_reader_frames_an_indefinite_blocks_payload`（载荷含引号与 `#`、无终结符、definite 块三例）；`test_scpi_message_reader_splits_and_limits` 原样过（#1449/#1497 规则不回退）；**行为变化披露：indefinite 块载荷含 LF 时，旧实现按 LF 截断成多条、新实现按块语义归一条** |
| **事故记录**：件 2 初版编辑吞掉了 MessageReader 内层 while 的循环尾 `self.pos += 1`——普通字符永不前进、首个字节即死循环；test_server 验证后台跑了 3.5 小时 100% CPU（~206 分钟 CPU 时间）后被杀。HEAD 对照 + faulthandler 栈抓到单行根因，恢复循环尾 + 三例 framing 单测钉住后 131 过 17 秒 | ✅ 已修；教训入册：**改扫描器循环必须带一个"普通字符前进"的用例先行**，且后台长跑要有墙钟上限 |
| ty/lint | ✅ 463 diagnostics 无新增；ruff 绿 |
| ty/lint | ✅ 463 diagnostics 无新增；ruff 绿 |
