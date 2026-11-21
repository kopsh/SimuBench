## scpi 包语法裁决三处收口：short_form 一处、SI 进位循环一处、块语法对齐（Issue 1766）

| 完成条件 | 自检 |
| --- | --- |
| **short_form 一处**：`parse.Pattern.compile` 的行内推导改调 `values.short_form`（#1074「带数字的词保留数字」的裁决注释随迁到 values 侧） | ✅ `test_command_equivalence`+`test_command_runner`+`test_units_scpi`+`test_builtin_commands` 1057 过；parse→values 单向 import，无环 |
| **SI 进位循环一处**：`_exp3_with_carry(v, mantissa_of, cap=)` 统一三个 format 函数的「999.5 进位到下一前缀」——mantissa_of 参数化三种拼写（si 的 significant-f 格、engineering 的 %.6g、digits 的 whole/decimals 格），cap 承载 si 的 ±12 阶梯；**符号随 mantissa 走（engineering 的 `-3.52334E+12`），digits 的 lead 独立（mantissa 取绝对值）** | ✅ **数值等价对照 sweep：78k+ 组（±13 位量级随机 8k 值 × 参数矩阵）零失配**——sweep 还逮住统一初版的两处符号破坏（engineering 丢负号、digits 双负号），逐个修掉；`test_units_scpi` 等原样过 |
| **块语法对齐**（parse.py 的 #0/#H/#Q/#B 裁决 vs MessageReader 的 1<=n<=9）→ 见独立条目/评论（涉及 indefinite block 的流式语义裁决，单独实现） | ⏳ |
| ty/lint | ✅ 463 diagnostics 无新增；ruff 绿 |
