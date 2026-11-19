## UXR 过冲/预冲：下降沿分支有测试，direction 参数登记与手册一致（Issue 1351）

| 完成条件 | 自检 |
| --- | --- |
| 下降沿的过冲/预冲有数值断言 | ✅ `tests/test_measurements.py::test_overshoot_and_preshoot_of_a_first_falling_edge_mirror_the_rising_one`：首个边沿为下降沿、直方图顶/底，过冲 10 %、预冲 5 %，边沿后的更深尖峰不计；分支本身没有缺陷，无需修复 |
| 覆盖表与手册的 `direction` 一致 | ✅ 手册 `:MEASure:OVERshoot/PREShoot [<source>[,<direction>]]` 有 RISing/FALLing（从左起第一个上升/下降沿），定义原先没登记；两个 UXR 定义登记 `direction`，两个值按 `not_modeled` 回 -224（不静默按第一个边沿测），覆盖表两型号各两行写明 |
| 手册用例 | ✅ UXR0334B、UXR1104B 的 `cases.yaml` 各加 `direction` 被拒为 -224 的步骤（96/96、124/124 通过） |
