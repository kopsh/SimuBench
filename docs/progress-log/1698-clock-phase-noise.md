## 采样时钟慢漂移：固有抖动随采集时长增长（Issue 1698）

| 完成条件 | 自检 |
| --- | --- |
| UXR1104B 在 1 µs、10 µs、100 µs、1 ms 采集时长下的 TIE 固有抖动各在表值（15 / 25 / 40 / 50 fs）±15 % 内 | ✅ `tests/test_scope_adc_nonlinearity.py::test_the_uxr1104b_intrinsic_jitter_grows_with_the_acquisition_span`：`timebase.clock_tie_s`（[采集时长, rms] 行，首行为白抖动）把时钟的慢漂移拟合成随机相位的正弦，各通道共有；UXR0334B 同表 |
| 同一种子逐位复现；任意一段单独算出与整段一致 | ✅ 同一测试比对分段与整段（按绝对时刻在固定节点上算再插值，逐分量累加，结果与段长无关） |
| 现有指标测试不变、不放宽 | ✅ 抖动、显示抖动、深记录金标准的测试未改，`scripts/check.sh` 通过 |
| 文档：11.2 去掉“没有建模”一条，11.4 指向本 issue | ✅ `docs/real-models.md` |
