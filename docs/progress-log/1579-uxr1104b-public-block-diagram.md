## UXR1104B 的内部结构按公开框图拆分，指标按数据手册的测量条件对照（Issue 1579）

| 完成条件 | 自检 |
| --- | --- |
| 公开资料入库 | ✅ `manuals/3123-1371/`（UXR-B 技术概述，PDF 走 LFS，附文本版），`manuals/README.md` 一行 |
| 结构与公开框图一致：输入 → 衰减器 → InP 前置放大器 → 采样器（4 路 SiGe）→ 16 路 ADC，时基为共用时钟 | ✅ 新通用组件 `scope_attenuator`、`scope_sampler`（`scope_input.py`），`scope_vertical.attenuator`、`scope_adc.sampler` / `clock`、`scope_timebase.clock_jitter_s`；`tests/test_keysight_uxr.py::test_the_front_end_follows_the_public_block_diagram`（四档衰减、增益补回、各路平铺），`tests/test_internals.py`（连线、触发在采样器之前分出） |
| 未接入新块的型号行为不变 | ✅ 接入 UXR1104B 之前全量测试 5109 passed（含采样流金标准逐字节） |
| 交织：数据手册的 SFDR 窗口、信号相关像、残余与 64 GHz 时钟杂散 | ✅ `tests/test_scope_adc_nonlinearity.py::test_the_uxr1104b_interleave_meets_the_data_sheet_in_its_measurement_windows`、`test_the_uxr1104b_residual_spurs_meet_the_data_sheet`；ENOB 用例原值通过 |
| 抖动：单通道 15 fs、通道间 < 10 fs | ✅ `test_the_uxr1104b_jitter_is_mostly_the_shared_clock`（15 fs、7.1 fs） |
| 显示抖动 71 fs（此前 102 fs） | ✅ `tests/test_keysight_uxr.py::test_the_display_jitter_meets_the_data_sheet`（55 GHz 信号源：`tests/definitions/test-sg-67ghz.yaml`）；触发通路不加噪声，旧机制用隐藏参数保留在 `test_the_trigger_taps_the_analog_front_end_with_its_own_noise` |
| 上升时间 | ✅ `test_the_trigger_sees_the_analog_front_end_and_the_record_its_dsp_correction`（3.7 / 2.6 ps 对数据手册由公式算出的 4.0 / 2.8 ps，10 % 内） |
| 文档 | ✅ `docs/real-models.md` 第 11 节（结构表、指标表、估计与未建模项），`docs/blocks/oscilloscope.yaml`、`docs/component-map.md`、`docs/signal-chain.md`、UXR1104B 覆盖表与定义注释 |
