## HEADers? 列出的 *TRG 与 classify／运行时一致（Issue 1565）

| 完成条件 | 自检 |
| --- | --- |
| `*TRG` 只在实例能执行时列出 | ✅ `commands.offers_bus_trigger`（`components` 与 `channel_components` 上带 `bus_trigger` 操作）是唯一判定：`answered_builtins` 没有它就不列，`classify` 没有它回 “模型没有总线触发组件”；运行为 -113（`undefined_header`，与 D42／`metrology-authoring.md` 的“未建模”一致） |
| `classify` 与运行时一致 | ✅ `COMMON` 补上 `TRG`，`*TRG` 走公共命令分支（方向 `{False, True}` 来自 `COMMON_DIRECTIONS`）；`tests/test_command_equivalence.py` 只在有 bus_trigger 时替换 `_bus_trigger`，没有时由真实方法拒绝，两边判定相同 |
| 全型号无多无漏 | ✅ 新增 `tests/test_help_headers.py`：每个库型号（含每个选件组合）`HEADers?` 的每一行都被 `classify` 支持；定义里按选件的每条命令都在列表里。需要参数的头只查头匹配（参数值不参与头匹配） |
| LeCroy WavePro 804HD 的 `*TRG` 真正可执行 | ✅ 手册（coverage 表）：`*TRG` 同 `ARM_acquisition`；定义补 `{header: "*TRG", op: acq.single, query: false}`，帮助行由该命令给出，不再依赖内置列表 |
| 覆盖表与文档 | ✅ `lecroy-wavepro804hd.coverage.yaml` 原就记 `*TRG` implemented，现与实际一致；`docs/metrology-authoring.md` 的“有 bus_trigger 才回答，否则未建模”未变，无需改 |
| 检查 | ✅ `UV_PYTHON=3.12 scripts/check.sh` 全绿（pytest 5109 passed / 9 skipped；ruff、ty、schema、web 均通过） |
