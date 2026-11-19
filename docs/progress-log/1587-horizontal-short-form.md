## UXR 的 `:FUNCtion<F>:HORizontal` 接受短格式 MAN（Issue 1587）

| 完成条件 | 自检 |
| --- | --- |
| `:FUNC1:HOR MAN` 成功，之后 `:FUNC1:HOR?` 回 `MANual`；`MANU` 回 -224 | ✅ `scope_math_controls.op_h_field` 改用引擎的 `V.parse_enum(raw, ("AUTO", "MANual"))`（SCPI 词匹配：短格式 MAN、长格式 MANUAL，任意大小写、可带引号），不再手写 `word not in ("AUTO", "MANUAL")`；`MANU` 回 `-224,"Illegal parameter value"`，设置保持原值 |
| UXR 的 cases.yaml 加对应用例 | ✅ UXR0334B（97/97）与 UXR1104B（125/125）各加一例：`MAN`、`manual` 接受并回读 `MANual`，`MANU` 报 -224 且仍是 `AUTO` |
| 检查同一文件里其他手写的关键字比较 | ✅ `scope_math_controls.py` 里其余 `.upper()` 比较只比内部算子名（`_TIMEBASE_BOUND`、`CANONICAL`）和已存的设置值（`startswith("AUTO")`），不是用户输入；该文件的数值解析不捕获 `ValueError`（-300）是 #1586 的范围，另一个代理在做，这里不动 |
