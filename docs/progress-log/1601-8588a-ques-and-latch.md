## 8588A 的 QUEStionable 无来源与 `latch: false`（Issue 1601）

| 完成条件 | 自检 |
| --- | --- |
| QUES 位有来源，或相应覆盖行不再标 implemented | ✅ 走第二条路：`STATus:QUEStionable[:EVENt]` 覆盖行改为 `accepted`、`affects_data: false`，备注写明手册的事件位（0 电压、1 电流、9 欠量程、10 过量程、11 触发过快、12 测量过快）在模型里都没有来源、恒读 0——不是「没有过载」的答案，过载读数（`overload_value` 9.91E+37）不置 OVER 位，来源接上前不要用它判断过载。没有用 `not_modeled`：定义里这条命令是回答的，覆盖检查不允许已定义的命令标未实现。`:CONDition`（手册：条件寄存器 Product 里不使用，读 0）与 `:ENABle`（使能读写与状态字节汇总按规则工作）保持 `implemented`。给 QUES 接过载来源要组件暴露 `status_flags`，留作后续（核心能力） |
| 位 6 WARM 无来源在覆盖表明示 | ✅ `STATus:OPERation[:EVENt]` 的备注已写「位 6、10、11 在模型里没有来源，恒为 0」（#1599 时写入），不需再改 |
| `latch` 参数在 authoring 文档中说明，并有独立单测 | ✅ `docs/instrument-authoring.md` 新增「`status_register` 的 `latch`」一段（默认 true，每次分派采样；`latch: false` 只在读时采样；为什么 8588A 需要它、代价是什么），`docs/component-map.md` 的 `status_register` 行补一句；`tests/test_status_register.py::test_latch_false_leaves_the_condition_sampling_to_explicit_reads`：默认每次分派采样，`latch: false` 不采样。没有去修「采样不应解算电路」的根因，那是另一个更大的改动 |
