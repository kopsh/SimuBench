## #1559 验收补齐：`:SYSTem:PRESet` 与 `*PSC` 的取舍记录、覆盖表同步（Issue 1568）

| 完成条件 | 自检 |
| --- | --- |
| `:SYSTem:PRESet` 的处理有记录并有 flow 测试 | ✅ D92 记“不做默认内置、型号自己写才回答”；`test_system_preset_is_no_default_builtin_but_a_model_s_own_answers` 逐步断言无定义回 -113、型号自带时设置回默认、使能不变。DAQ970A、B2900、34970A 的 Preset 与 `*RST` 手册上不同，覆盖表保留 `not_modeled` 并写明 D92，是最终等级而非遗漏（issue 期望“不留 not_modeled”在此未做到：做出差别需要各型号 Preset 的手册依据与设置块） |
| `*PSC` 对 ESE/SRE 上电值的处理与回放结论 | ✅ D92：只读存根，没有掉电重启所以无可观察作用；不进快照，回放靠重发命令，`test_the_psc_flag_replays_with_its_command` 断言默认 1、`*PSC 0` 回放后一致、快照不含 psc |
| 定义了 `*PSC` 的覆盖表同步 | ✅ 33622A、DAQ970A 的 `*PSC` 由 `not_modeled` 改 `accepted`（两者 `builtins` 补 `*PSC`，各加手册用例）；34465A 的备注改为手册默认值 1 |
| 手册核对 | ✅ 34465A（34460-90901 `*PSC {0|1}` 默认 1）、33622A、DAQ970A 手册给默认 1；Fluke 8588A Remote Programmer's Manual 只列命令未给默认值，备注如实写“手册未给”，34465A 的“手册未给”原写法已更正 |
| 其余型号 | ⚠ 34970A 无手册入库，`*PSC` 未动；`:SYSTem:VERSion?` 各表已为 `implemented`，bk-8614 手册无值保持 `not_modeled` |
