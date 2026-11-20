## UXR0334B 补上采样器块，交织失配与抖动按结构重新分配（Issue 1693）

| 完成条件 | 自检 |
| --- | --- |
| 每通道 `ch<n>.sampler`（`scope_sampler`，`paths: 2`，`rate = specs.sample_rate`），接成 front → sampler → adc；`adc` 写 `sampler: sampler`，第 i 路属于第 i mod 2 个采样器 | ✅ `keysight-uxr0334b.yaml` 新增 `sampler` 通道块（2×64 GSa/s SiGe，3123-1371 p.12），连线 `ch{n}.probe.out → ch{n}.sampler.in → adc.in{n}`；`adc.sampler: sampler`、`clock: timebase`、`interleave: 8`。结构照 p.12（无 InP 主采样器，两通道共用一块采集板），拆解视频 tsp-133 28:40（issue 引）证实只用 SiGe |
| 交织失配重新分配：采样器 2 路偏置/时延 + adc 8 路失配，合起来满足数据手册 3123-1313 p.10 的 SFDR、信号相关像、残余杂散、时钟杂散 | ✅ 8 路时延取谐波 k=1、2、3（各 5 fs、相位 πk²/6），偏置 k=1 正弦（16 GHz -65 dBFS）；2 路采样器偏置 `[-2.8117e-4, +2.8117e-4]`（零均值，64 GHz 奈奎斯特 bin 按 \|X\|/N，-65 dBFS）、时延 5 fs。`test_the_uxr0334b_interleave_meets_the_data_sheet_in_its_measurement_windows`（5 GHz 0–10 GHz ≤ -65、50 GHz 40–60 GHz ≤ -61、60 GHz 信号相关像 ≤ -52）、`test_the_uxr0334b_residual_spurs_meet_the_data_sheet`（16/64 GHz -65 dBFS，其余不超过）。p.10 表按 256 GSa/s（1 mm）给出，3.5 mm 无单独表，沿用同组限值（#1422 起即如此） |
| 抖动拆分：`timebase.clock_jitter_s` 共享、`sampler.aperture_jitter_s` 每通道，单通道 15 fs rms、通道间 < 10 fs rms（p.11） | ✅ 时钟 √(15²−5²) ≈ 14.14 fs、每通道孔径 5 fs、adc 自身置 0；`test_the_uxr0334b_jitter_is_mostly_the_shared_clock`（单通道 15 fs、通道间 7.1 fs）。两通道共用采集板但各自 2 个 SiGe 采样器独立，时钟经 timebase 全机共享（p.13） |
| UXR0334B 的 Web 仪器内部页显示出采样器块 | ✅ `tests/test_internals.py::test_the_uxr_probe_block_says_it_converts_settings_and_the_two_vertical_blocks_differ` 对两型号都断言 `probe → sampler → adc`，触发在采样器之前分出 |
| 现有 UXR0334B 指标测试全部通过，不放宽容差 | ✅ `tests/test_keysight_uxr.py`、`tests/test_scope_adc_nonlinearity.py` 全过（67 passed）；新增三个测试与 UXR1104B 同类，未改任何既有容差 |
| `label` 与注释写明页码；更新 `docs/real-models.md` 与覆盖表相应条目 | ✅ 定义注释与 `sampler.label`/`description` 引 3123-1371 p.12/p.13、3123-1313 p.10/p.11；`docs/real-models.md` 删去 11.2 末尾「仍是旧结构」、新增 11.3（UXR0334B 结构表与指标表）；覆盖表 `:WAVeform:DATA` 说明改为两级失配与抖动拆分 |
| `scripts/check.sh` 通过 | ✅ lint、format、类型、全量测试（5375 passed）、各 check 脚本、schemas（无变化）、web 检查（405 passed）均过。采样流金标准重录（`tests/record_gold.py scope-stream`），仅 `keysight-uxr0334b` 的 7 个摘要变化，其余型号逐字节不变。全量测试在本沙箱（3 核）约 257 s，为既有基线（最慢用例均为既有计算型测试，非本次新增；75 s 预算按 CI 硬件标定），本次新增 3 个测试 < 1 s |
