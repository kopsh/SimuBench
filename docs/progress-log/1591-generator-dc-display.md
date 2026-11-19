## 函数发生器 DC 功能下不再显示频率与幅度（Issue 1591）

| 完成条件 | 自检 |
| --- | --- |
| 画布节点在 DC 下不显示频率和幅度 | ✅ `unusedUnderDc`（`features/workbench/model.ts`）按设置名判断：同一通道的 `function` 为 DC 时，frequency、amplitude、phase、占空比、对称度不进节点摘要；SDG2042X 的 CH1 读作 `DC 1 V OUT 开`（Playwright 截图核对） |
| 仪表屏幕在 DC 下不误导 | ✅ 大读数显示偏置（`1 V`）而不是频率，不显示幅度；不生效的字段变暗并注明「直流输出时不生效」（仍可编辑，切回正弦时生效），预览标题写 DC 而不是周期数 |
| 检查器一致 | ✅ `inactiveReason` 统一给出原因（自动开关优先，其次 DC），`StateField` 按原因写注释；只有自动开关时数字框才读作「自动」 |
| 测试 | ✅ `model.test.ts` 三条（DC 下不用的设置、只看同一通道的功能、原因的先后）；`pnpm typecheck / lint / test` 全过 |
| TUI | ✅ 更正（#1595）：原先写的「终端没有发生器的通道摘要行，无需改动」与代码不符——`tui/cards.py` 的 `generator_rows` 每个通道一行，原来 DC 下仍显示频率与幅度；#1595 已改为 DC 下只显示偏置，仪表屏幕上不生效的字段变暗并在通道盒子的原因行说明 |
