## 示波器光标与数学水平参数：非数字参数回 -104，命令失败不改方式（Issue 1586）

| 完成条件 | 自检 |
| --- | --- |
| 三处解析把 `ValueError` 转成 `ScpiError(-104)` | ✅ `scope_cursors._marker_number`、`scope_math_controls.op_h_field` 捕获 `ValueError`（`UnitError` 亦为其子类）回 `-104`；`op_scale` 此前已捕获 |
| 先解析、后写光标模式 | ✅ `scope_cursors._position` 改为先绑定设置、解析数值，再写 `marker_mode`；参数被拒时方式保持原值 |
| 回归测试：表中命令发送 `MAX`/`INF`/`NAN`/`""`/`ON` 无 -300、设置不变 | ✅ `tests/test_non_finite_settings.py` 的遍历（原 #1495）按 `op` 名扩展出 `meas.x_position`/`meas.y_position`/`meas.tracked_y`/`math.h_field` 的 raw 参数命令，五个词都断言错误队列不含 -300 且 `settings.values` 不变（含 `:MARKer:MODE?`） |
| 用例在旧代码上失败 | ✅ 还原 `src` 后 7 个测试台（DSO-X 2024A、33622A、M8194A、UXR0334B、UXR1104B、DSG815、SMA100B）按 issue 所述报 `[-300]`；修复后该文件 28 条测试全部通过 |
