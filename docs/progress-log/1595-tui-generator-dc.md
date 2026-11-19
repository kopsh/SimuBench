## TUI 函数发生器在 DC 功能下不再显示频率与幅度（Issue 1595）

| 完成条件 | 自检 |
| --- | --- |
| `generator_rows` 在 function 为 DC（不分大小写）时输出偏置而不是频率/幅度，其余功能不变 | ✅ `tui/cards.py`：DC 下通道行是 `CH1 On DC 1.000 V`（偏置），`dc` 小写同样；SINE 仍是 `CH1 On SINE 1.000 kHz 4.000 V`；`tests/test_tui_units.py` 断言两种输出的文本 |
| 仪表屏幕在 DC 下不把频率/幅度当作有效信号呈现（或注明不生效） | ✅ `tui/controls.py` 新增 `inactive_under_dc`（字段集 `UNUSED_UNDER_DC` 与 Web 的同名集合一致：frequency、amplitude、phase、duty、sq_duty、pulse_duty、symmetry、ramp_symm）；按钮里这些字段的标签与值变暗（与 Web 的变暗一致），仍可编辑；通道盒子的原因行写「Not in effect for a DC output: Frequency, Amplitude」，措辞与 Web `workbench.dcInactive` 逐字一致（新增共享键 `cards.dcInactive`，`scripts/check_tui_i18n.py` 守卫）；`tests/test_tui_t2.py` 的启动级测试：CH1 为 DC 时有这一行，CH2（SINE）没有 |
| 更正 `docs/progress-log/1591-generator-dc-display.md` 里「TUI 无需改动」的说法 | ✅ 该行改为说明原先与代码不符、已由 #1595 改 |
