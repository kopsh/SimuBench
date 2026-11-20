## UXR 触发通路定量：保留模拟通路，UXR0334B 的触发噪声去掉、显示抖动对上数据手册（Issue 1691）

| 完成条件 | 自检 |
| --- | --- |
| 两种方案的显示抖动都实测 | ✅ 数据手册工况（100 mV/div、500 mVpp 正弦），各 300 次采集：模拟通路 UXR1104B 70.9 fs、UXR0334B 117.3 fs；数字触发（用记录自己的采样定触发时刻：同组采样线性插值、8 倍多相上采样、平滑副本）UXR1104B 0 / 14 / 33 fs、UXR0334B 0 / 12 / 25 fs。数字触发的数据由一次性实验得到，写在 `docs/real-models.md` 11.3，不进测试 |
| 选定方案并写明理由 | ✅ 保留 ADC 之前的模拟通路：3123-1313 p.16 的 71 fs 与 116 fs 都正好等于记录噪声 ÷ 斜率，只有触发时刻与记录噪声无关才会这样；理由写进 11.1、11.2、11.3 |
| 所有用 `analog_response` 的定义一并评估 | ✅ 只有两台 UXR。UXR0334B 的触发通路还留着 #1298 的通道噪声表，显示抖动 181 fs（数据手册 116 fs）：按 #1579 的做法去掉，定义与覆盖表的注释同步 |
| 显示抖动测试仍通过，并覆盖 UXR0334B | ✅ `tests/test_keysight_uxr.py::test_the_display_jitter_meets_the_data_sheet` 参数化为 uxr1104b（71 fs）与 uxr0334b（116 fs），±15 %；`test_the_trigger_path_carries_no_noise_table` 取代 `test_the_trigger_noise_is_the_channel_s`；`test_the_trigger_taps_the_analog_front_end_with_its_own_noise` 两个型号都先断言默认不触发，再用隐藏参数还回噪声表检查机制 |
| 金标准随有意的行为变化重录 | ✅ `uv run python tests/record_gold.py scope-stream`：只有 keysight-uxr0334b 的 8 步（触发后的 `:WAVeform:PREamble?`、`:WAVeform:DATA?` 与屏幕帧）摘要改变，各步虚拟时间不变，其他型号逐字相同 |
| 检查通过 | ✅ `scripts/check.sh` |
