## 所有仪表默认支持 :SYSTem:HELP:HEADers? / SYNTax? / SYNTax:ALL?（Issue 1558）

| 完成条件 | 自检 |
| --- | --- |
| 列出的命令与实际能执行的一致 | ✅ `tests/test_builtin_commands.py`：命令方向标记、未装选件的命令不列出、白名单只收窄其余内置命令；`tests/test_command_equivalence.py`：三条命令的路由与 `classify` 一致（每个内置型号） |
| 排版按厂商预设 | ✅ `tests/test_builtin_commands.py`：keysight 预设的块头长度与载荷一致、CR LF；auto 按厂商选 keysight_plain / rs；UXR1104B 写 `help: {preset: keysight}` |
| 开关与覆盖 | ✅ `help: {enabled: false}` 回 -113（`builtin_excluded`）且不列出；定义里的同名命令头优先 |
| 语法查询 | ✅ `SYNTax? "<头>"` 长短写法都认，未知命令 -113，缺参数 -109；`SYNTax:ALL?` 每命令一行 |
| 覆盖表与手册用例 | ✅ M8194A、N9042B 的 `:SYSTem:HELP:HEADers` 改为 implemented，各加一条手册用例（`tests/models/*/cases.yaml`） |
| 未核实 | R&S 的 `HEADers?` 是否带块头、查询不存在命令的真机错误、其他系列 Keysight 是否带标记：手册没写，按 issue 的假设实现，拿到真机样本再改 |
