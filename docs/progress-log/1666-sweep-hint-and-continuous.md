## 频谱仪：去掉提示栏，换成真机的“连续扫描”“单次扫描”两个键（Issue 1666）

| 完成条件 | 自检 |
| --- | --- |
| 去掉提示栏：它的文字与按钮已由顶栏（已暂停 / 实时 / 推进）和扫描键承担 | ✅ `SpectrumScreen.tsx` 不再渲染 `sweep-hint`；`spectrum.advanceSweep`、`liveSweep`、`continuousOn` 等键删除；`SpectrumScreen.render.test.tsx` 断言没有提示栏 |
| 像真机一样并排“连续扫描”“单次扫描”，当前模式那个键亮起 | ✅ `SweepKey`（`aria-pressed`、实心为当前模式）；连续发 `:INIT:CONT ON`，单次发 `:INIT:CONT OFF;:INIT:IMM`；设置里的“连续扫描”开关不再重复（按屏幕字段登记，检查器也不重复） |
| 暂停时按“连续扫描”就扫起来（同时切到实时并提示）；实时时不改时钟；脚本的 `:INIT:CONT ON` 不改时钟 | ✅ 渲染测试 6 个用例；e2e `instruments.spec.ts`（暂停时单次扫描前进一轮后 HOLD、推进不扫、脚本开连续时钟不变、屏幕开连续切到实时且计数增长、实时下单次后计数停住） |
| 文案说结果；`HOLD` 悬停说明 | ✅ `spectrum.hintClock` / `hintSingle` 改写（TUI 共享，快照更新）；`HOLD` 的 title 为 `spectrum.hintSingle` |
