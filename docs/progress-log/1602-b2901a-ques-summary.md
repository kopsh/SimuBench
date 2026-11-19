## B2901A / B2912B：QUEStionable 的汇总位取自子寄存器（Issue 1602）

| 完成条件 | 自检 |
| --- | --- |
| QUES 的位 0/1/4/8/9 取自对应子寄存器（Table 4-7 至 4-11）的汇总：子寄存器事件 & 使能 ≠ 0 时才置位；`STAT:QUES:CURR:ENAB 0` 后 `STAT:QUES:COND?` 不再保留位 1 | ✅ 定义的 `ques.bits` 改为 `QUES_VOLT`/`QUES_CURR`/`QUES_TEMP`/`QUES_CAL`/`QUES_TEST`（B2912B 按 `CH1.`/`CH2.` 各一份）；复现序列：`CURR:ENAB 1` 时 `COND?` 2、`*STB?` 8，`CURR:ENAB 0` 后 `COND?` 0 |
| 用例补上限值到达时子寄存器条件为 1、子寄存器使能对 QUES 汇总与 `*STB?` 的影响（B2901A 与 B2912B 各一条），测试同时钉住修复 | ✅ `tests/models/keysight-b2901a/cases.yaml`、`keysight-b2912b/cases.yaml` 各新增「可疑汇总」用例；`tests/test_keysight_b2901a.py` 两条、`tests/test_keysight_b2912b.py` 一条；旧定义上这三条测试失败（3 failed），新定义上全部通过 |
| B2912B 按手册区分通道 | ✅ 每个通道的 QUES 只汇总本通道的子寄存器：用例与测试设置两路都在限流，使能通道 2 的 CURR 子寄存器后 `STAT:QUES:COND? (@2)` 为 2、`(@1)` 为 0 |
| 覆盖表说明同步；`scripts/check.sh` 全绿 | ✅ 两个型号的 `QUEStionable:CONDition` note 改为「位 0/1/4/8/9 是子寄存器汇总」；`uv run simubench cases` 24/24、35/35；`scripts/check.sh` 全绿 |
