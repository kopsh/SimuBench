## IEEE 488.2 公共命令检查参数（Issue 1453）

| 完成条件 | 自检 |
| --- | --- |
| `*ESE`/`*SRE` 越界报 -222、寄存器不变 | ✅ `BuiltinCommands._register_mask`：0–255 以外（256、-1、999、20 位整数）报 -222，不再按 8 位截断；`tests/test_builtin_commands.py` 参数化 2×4 例，断言报错后寄存器仍是原值 |
| 缺参数 -109、多余参数 -108 | ✅ `check_params`：`*ESE`/`*SRE`/`*PSC` 写入须恰好一个参数；`*ESE 1,2`、`*PSC 1,0` 报 -108，`*ESE`（无参数）报 -109 且不清零 |
| 不带参数的公共命令带参数报 -108 且不执行 | ✅ `*IDN? *OPT? *RST *CLS *OPC *OPC? *ESR? *STB? *TST? *WAI *TRG` 与 `*ESE? *SRE? *PSC?` 带参数报 -108；`*RST 1`、`*CLS 1` 不复位、不清队列（单测断言设置与队列不变）。检查在各处理函数内（`_common`；绕过它的 `*OPC?`/`*WAI`/`*TRG` 在 `_dispatch`），`test_command_equivalence.py` 的路由对照不变 |
| 现有 `tests/models/*/cases.yaml` 仍通过 | ✅ 全部型号手册用例跑过，无用例依赖旧行为 |
| 个别型号的例外由定义声明 | 无型号手册要求容忍越界或多余参数，没有加例外 |
