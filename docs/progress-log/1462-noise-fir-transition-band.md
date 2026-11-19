## 噪声整形 FIR 的过渡带写进文档，测试覆盖 113–118 GHz（Issue 1462）

| 完成条件 | 自检 |
| --- | --- |
| 二选一：加大抽头，或保留设计并把验收/文档改成如实的过渡带 | ✅ 取 (b)：保留 49 抽头 Hann 窗（不动噪声数值、不动现有金标准、不增加深记录的卷积开销）。`docs/signal-chain.md`：砖墙在截止频率处 −6 dB，截止频率上方约 5 GHz（0.02 倍采样率）处起低于 −20 dB，截止到奈奎斯特平均约 −15 dB，113–118 GHz 是过渡带；高斯形本身只衰减约 3 dB |
| 测试覆盖 113–118 GHz 区间，不再只测 >118 GHz | ✅ `tests/test_scope_adc_nonlinearity.py::test_the_brickwall_noise_filter_has_the_transition_band_the_docs_state`（带内平坦、−6 dB、118.5 GHz 处 −20 dB、113–118 GHz 平均 −10 dB、113–128 GHz 平均 −15 dB）；UXR 整机测试的文字改成 118 GHz 以上 |
| 附带：高斯响应下 113–128 GHz 仅 −2.8 dB 属预期，docs 注明 | ✅ 同一文档段，`test_the_gaussian_noise_shape_falls_only_about_3_db_past_the_cutoff` 断言 −2.8 dB ± 0.5 |
