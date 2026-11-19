## 计算预算在 Windows 上用高精度时钟计时（Issue 1538）

| 完成条件 | 自检 |
| --- | --- |
| main 变绿 | ✅ Windows 上 `test_a_run_that_ran_out_of_budget_replays_identically_and_only_because_the_record_says_so` 稳定红（两次 main 都红）：`ComputeBudget` 用 `time.monotonic()`，Windows 上约 15.6 ms 一跳，1 ns 的预算在一跳之内做完的动作里永远读不到超时，于是没有 `abort` 事件。改用 `time.perf_counter()`（Windows 上亚微秒） |
| 有回归测试 | ✅ `test_a_budget_shorter_than_a_coarse_clock_tick_still_runs_out`：monotonic 冻住、perf_counter 走 1 µs，预算即告罄（还原修复则红） |
