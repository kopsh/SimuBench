## #1451 验收的用例缺口：N9320B `:BAND` 缺参、错误后设置不变、UXR 完整错误文本、`#H` 数值（Issue 1496）

| 完成条件 | 自检 |
| --- | --- |
| `keysight-n9320b/cases.yaml` 增加 `:BAND` 缺参 -109 用例 | ✅ 新用例：先设 100 kHz、`:BAND` 无参数报 `-109,"Missing parameter"`，之后带宽与自动耦合开关（0）不变、错误队列清空 |
| 各新用例在错误前后各读一次设置并断言相等 | ✅ 33622A `:FUNC:PULS:PER`、DSO-X `:FUNC:SCAL`（缺参与 `MAX` 各读一次）、N9042B `:BAND`、UXR0334B `:MARK1:X:POS`（缺参与 `MAX` 各读一次）都先写一个值、被拒后再读并断言没变 |
| UXR 光标用例改为完整错误文本断言 | ✅ 改用 `:SYSTem:ERRor? STRing`（UXR 的 `:SYST:ERR?` 默认只回错误码，文本要用 `STRing`）：`-109,"Missing parameter"`、`-104,"Data type error"`、`0,"No error"` |
| 核对 `#H1F` 的期望行为并补用例，或写明依据后保持现状 | ✅ 实测现在是 -104 且刻度不变（#1497 之后不再是 -161）。IEEE 488.2 7.7.4 的非十进制数值 `#H/#Q/#B` 是数字，平台在别处都这样解析（34465A `*ESE #HFF`），只有 `scope_math_controls` 的刻度解析自己用 `parse_number` 漏掉了：改为 `#` 开头时走 `parse_float`；新用例 `#H2`→2、`#B11`→3、`#HZZ`→-104 且保持 3。DSO-X 的编程手册不在 `manuals/`，用例的 `manual` 写的是 IEEE 488.2 7.7.4，不是仪表手册 |
