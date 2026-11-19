## 状态寄存器的使能不随 *RST 清零（Issue 1600）

| 完成条件 | 自检 |
| --- | --- |
| 34465A：`*RST`、`SYSTem:PRESet` 后 OPER/QUES 使能保持，`*CLS` 不改，`STAT:PRES` 清 0 | ✅ `status.oper_enable` / `status.ques_enable` 标 `keep_on_reset: true`（`*RST` 与预设都不动，上电仍回 `default`）；手册 34460-90901：enable 设置非易失，`*RST`、`SYSTem:PRESet`、`*CLS` 都不改 |
| 新增用例 | ✅ `tests/models/keysight-34465a/cases.yaml`：设使能 → `*RST` → 读、`*CLS` → 读、`SYST:PRES` → 读、`STAT:PRES` → 0 |
| 同类型号逐一对照 | ✅ 53220A / 53230A、8588A、B2901A、B2912B 的使能同样随 `*RST` 被清，与各自覆盖表写的「`*RST` 不改」矛盾；手册没有单独写这几个型号的 `*RST` 行为，按 IEEE 488.2 11.5.1.2（`*RST` 不改状态报告的使能）一并标 `keep_on_reset`，各有一个用例（53220A / 53230A 的手册未列 `STATus:PRESet`，用例不测它）。B2900 的 PTR/NTR 过滤不在 issue 范围，未改 |
