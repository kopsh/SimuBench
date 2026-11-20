## 频谱仪 E2E：扫频计数等稳定再定格，状态断言容忍慢机器（Issue 1686）

| 完成条件 | 自检 |
| --- | --- |
| 「实时下单次停住」断言改为：等计数连续两次读数相同（有界 30×250ms）再定格，再等 400ms 断言不变 | ✅ `web/e2e/instruments.spec.ts`（settle helper）；根因：1ms 扫频 × 实时时钟跑在墙钟前，先读 stopped 再比必输给慢机器（CI Expected 837/899 两次、本地复现） |
| 点击后的状态断言（aria-pressed/HOLD/实时）放宽到 15s，语义不变 | ✅ 同文件三处 `{ timeout: 15_000 }`；本地重载下「单次后 5s 未变 true」的形态不再卡死 |
| 语义不变：仍验证实时下单次后计数停住、墙钟继续走、暂停键可用 | ✅ 测试步骤未增删，只改等待策略 |
| 本地 ≥3 次通过 | ✅ 修复后 6 连过（`playwright test -g "the Continuous and Single keys" --repeat-each` 多轮）；修复前计数形态稳定复现 |
