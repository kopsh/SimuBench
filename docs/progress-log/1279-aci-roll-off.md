## 万用表交流电流有自己的频响（Issue 1279）

| 完成条件 | 自检 |
| --- | --- |
| 定义可分别设置 ACV 与 ACI 的滚降 | ✅ `dmm_conditioning` 新增 `ac_high_current`（Hz，ACI 的 −3 dB 点，默认 `None` = 沿用 `ac_high`）；`true_value(function)` 让 ACI 走自己的滚降，其余功能不变；`true_values()` 的键不变（`verdicts.py` 与读数里的 `true_*` 不多出键） |
| 34465A 在 100 kHz 的 ACI 读数明显低于 1 kHz 的读数，有用例断言 | ✅ 手册 34460-90901「The effective AC voltage bandwidth is 300 kHz, while the effective AC current bandwidth is 10 kHz」（第 5898 行）；34465A 取 `ac_high_current: 1.0e4`（二阶，「有效带宽」不是 −3 dB 点的定义，滚降形状为估计）；`tests/test_meter_core.py`：组件级（100 kHz 的 ACI 低于 ACV 的 2 % 之内、1 kHz 两者相同、不设时沿用 `ac_high`）与用 34465A 自己的参数的断言（100 kHz < 1 kHz 的 2 %） |
| 其余型号 | 8588A、DAQ970A、34970A 的手册里没有 ACI 频响的依据（DAQ970A 手册只有 ACI 滤波器带宽设置，没有有效带宽），没有猜数，保持 ACI 沿用 `ac_high`；找到依据后只需在定义里加 `ac_high_current` |
| docs/component-map.md 与 coverage 说明同步 | ✅ |
