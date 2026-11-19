## 计算预算与取消覆盖不经过帧的长读取：频谱仪解析扫描、示波器深记录（Issue 1533）

| 完成条件 | 自检 |
| --- | --- |
| 盘点不经 `cached_frames` 的长循环 | ✅ 清单写进 D88「检查点在哪里」：要加的两处实测——N9042B 40 001 点、平均 10 000 次的 `:INIT` 178 s；UXR1104B 128 Mpts 深记录上的 `:MEASure:VPP?` 37 s，且 0.5 s 预算原先拦不住（分块按采集时的链快照直接算，不经过会话的帧，D88 原文说它被覆盖是错的）。不加的循环（触发搜索、屏幕包络、记录器补点、锁相读数轨迹、计数器时间戳）各写了为什么有界 |
| 加检查点 | ✅ `SpecanSweeper.sweep` 每算一扫之前（`at` 为 `<组件>.sweep`，覆盖 `:INIT`、连续平均与最大/最小保持）；`DeepRecord._tile` 每算一个新分块之前（`at` 为 `<组件>.record<n>`，由 `scope_capture` 传入 `Session.checkpoint`）。改后实测：同一 `:INIT` 0.3 s 预算 0.31 s 结束、`-221`；同一 VPP 0.5 s 预算 0.50 s 结束 |
| 各一例：−221、其后命令正常、回放一致 | ✅ `tests/test_compute_budget.py`：`test_an_analytic_sweep_average_ends_at_its_budget_and_replays_by_the_record`（N9042B；删掉 `abort` 事件后回放对不上）、`test_a_measurement_over_a_deep_record_ends_at_its_budget_and_replays_by_the_record`（UXR1104B 2 亿点）；预算取 `1e-9`，不靠计时 |
| 顺带修的缺陷 | ✅ 真实型号上 `ComputeAborted` 进错误队列时，错误报告器 `dataclasses.replace(err, text=…)` 因手写的 `__init__` 抛 `TypeError`（#1510 的用例只用验证仪表，没碰到）。`abort` 改为数据类字段，删掉装饰器为该类生成的冻结 `__setattr__`/`__delattr__`（`__traceback__` 仍可赋）；理由写在类文档与 D88。上面两例都走真实型号，锁定它 |
| 文档 | ✅ decisions.md D88；architecture.md 2.3 检查点一句 |
