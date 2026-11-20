## 频谱仪提示栏文案与按钮换行；暂停时在屏幕上打开“连续扫描”同时切到实时（Issue 1666）

| 完成条件 | 自检 |
| --- | --- |
| 提示说结果，不讲虚拟时间，也不在频谱仪屏幕上讲示波器 | ✅ `spectrum.hintClock`：“已暂停：时间不走，频谱仪就不扫频，迹线保持不动。”；`spectrum.hintSingle`：“单次扫描：连续扫描已关，迹线停在最后一轮。”；TUI 共享文案同步（`scripts/check_tui_i18n.py`），TUI 频谱仪快照随之更新 |
| 提示栏的按钮不换行 | ✅ 文字 `min-w-0 flex-1` 折行，按钮组 `shrink-0` 固定在右侧；1280 px 窗口截图确认 |
| 暂停时在屏幕上打开连续扫描：扫起来（同时切到实时并提示）；实时时开关不改时钟；关闭照常定格 | ✅ `SpectrumScreen.tsx` 的 `sweepContinuously`（设置开关的 `onCommit` 与提示栏按钮共用）；`SpectrumScreen.render.test.tsx` 三个用例；e2e `instruments.spec.ts`（暂停时开启连续扫描后顶栏为“实时”、扫描计数增长） |
