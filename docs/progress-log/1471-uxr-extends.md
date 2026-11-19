## UXR0334B 命令表继承 UXR1104B：extends + command_overrides 收单源（Issue 1471）

| 完成条件 | 自检 |
| --- | --- |
| extends + overrides | ✅ 0334b 改 `extends: keysight-uxr1104b`：command_overrides 85 头差异整组/单条覆盖（CAPability:ACQuire/DIGital 两个多变体头按列表值整组替换，#1480 的语义）+ 80 个仅父有头 null 删除 + 1 个本型号独有头（*OPT）追加；原 commands 整块退役，capabilities/format/syntax/errors/reference/builtins/error_queue 七节逐字相同改继承，文件 2660 → 2136 行 |
| 泄漏清除 | ✅ 父定义独有的键逐键 null：state 111（94 顶层 + 17 嵌套叶）、channel_state 8、specs 8（含 offset_range_v 四档与 input.connector）、ports.REF_IN、identity.options_reply、options/constraints 整键——01G/02G 选件与 1104B 专属设置不再泄入 |
| 对拍 | ✅ loader 全层合并后 model_dump 与原独立定义逐节相等（commands 逐头零差；列表顺序随父表位置属 D59 机制固有）；路由等价扫描 + test_keysight_uxr 802 例全过；金测 96/96 |
| 顺带 | ✅ front 组件的 probe_ratio 重复键（`$ch.probe` 与 `1.0`，loader 按 last-wins 生效 1.0）清为单值 1.0——原文件的潜在笔误；1104b 自述「the same command set as the UXR0334B」改为指向继承方向 |
| 文档 | ✅ progress-log 本条目（D59 已由 #1480 修订，不另立条目） |
| check.sh | ✅ 全绿（pytest 全量、web 全量、ty 零新增、schemas 零 diff） |
