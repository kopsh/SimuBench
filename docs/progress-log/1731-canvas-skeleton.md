## 仪器屏幕绘图骨架单源：频谱屏复用波形骨架，频谱刻度数学可单测（Issue 1731）

| 完成条件 | 自检 |
| --- | --- |
| 定尺寸/网格/抽稀/扫线动画/观察器抽成共享骨架 `mountPlot` + `plotSeries`（`screens/PlotCanvas.ts`），WaveformCanvas 与 SpectrumScreen 都改走，各留自己的标定与线光栅化 | `vitest run src/features/workbench/screens/`（36 例）+ `tsc -b --noEmit` + `eslint .` |
| 频谱私有数学（dB/div 刻度 `dbAt`/`dbLabels`、参考电平位置 `dbY`、标记落点 `markerPoint`）抽成导出纯函数，`SpectrumCanvas.test.ts` 不开浏览器直测 | `vitest run src/features/workbench/screens/SpectrumCanvas.test.ts`（7 例） |
| 频谱屏视觉输出像素不变：两屏网格线只差半像素虚线相位（端点都在边框之下被不透明边框盖掉），故线光栅化作为 `PlotLine` 参数各留原样，其余绘制次序逐段核对等价 | PR CI 的 Storybook visual job（本机是 darwin 字体环境，不产基线） |
| WaveformCanvas 现有测试（8 例）与 stories 原样存活，改动测试文件 ×5 稳定 | `WaveformCanvas.test.ts` 原样；`SpectrumCanvas.test.ts`+`WaveformCanvas.test.ts` 连跑 5 遍全绿 |
| `scripts/check.sh`：ruff/format/ty/其余脚本检查/schemas/check:api 全绿；pytest 仅 `test_select_tests.py::test_shared_code_runs_everything` 本机预算抖动（0.115s>0.1s，存量 #1716，单测重跑即绿，基线同红） | 本地 check.sh 日志 + 单测重跑 |
