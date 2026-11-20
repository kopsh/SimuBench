## PRBS 报错只列真正可建模的阶数，未截断的信号不再带 `truncated` 说明（Issue 1657）

| 完成条件 | 自检 |
| --- | --- |
| 阶数 13/15/31 的报错信息中的可用阶数列表为 `[5, 6, 7, 9, 10, 11]` | ✅ `sources.PRBS_ORDERS` 从 `PRBS_TAPS` 取一个 sinc 瓣（2^n − 1 条线）不超过 `MAX_LINES` 的阶数，超限与“不支持的阶数”两条 -224 共用它；`tests/test_sampling.py::test_a_pattern_whose_period_exceeds_the_line_limit_is_refused` 断言消息含 `modeled orders: [5, 6, 7, 9, 10, 11]` |
| PRBS11 的输出带 `truncated` 说明、PRBS5 不带，6 加入参数化 | ✅ 只有当 `lobes` 要的谱线数超过 `MAX_LINES`（真的切了）时才加 `FidelityNote("truncated", …)`；`test_a_pattern_whose_lobe_fits_the_line_limit_is_built` 参数化为 5/6/7/9（无说明）与 10/11（有说明） |
| `scripts/check.sh` 通过 | ✅ 全绿（lint、format、类型、全量 pytest、TUI i18n、测试规则、分层、skills、progress-log、改动测试 ×5、schemas、web） |
