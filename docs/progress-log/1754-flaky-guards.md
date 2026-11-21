## 1754 快层偶发红治理

| 项 | 结果 |
| --- | --- |
| 完成条件 | 见 #1754：select 库扫描缓存、本地单元预算余量、deep_record 墙钟余量；满载模拟下旧悬崖用例连跑绿 |
| select 缓存 | `_library(root)` 按（root, 目录签名）缓存 ids+children，签名=scandir name+mtime_ns+size；`model_ids`/`extends_of` 变薄封装，`select()` 单次取数。实测 9 次 select 99.0→**23.4ms** CPU（空载）；陈旧性回归测试 `test_the_library_cache_sees_a_file_added_between_two_selects` |
| 单元预算执法 | `UNIT_BUDGET_S` 本地 0.1→**0.15**（CI 0.3/Windows 1.0 不变）；D49 条目记执法曲线与依据（满载 CPU 时间膨胀实测 1.5–1.8×，99ms 用例读 0.101–0.165s）；`test_shared_code` 缓存后 23.4ms，对 150ms 余量 6.5× |
| deep_record 阈值 | 本地 3.0s（空载实测 0.70s 的 4.3×），**CI 保持 2.0s**；注释引实测；`load_sensitive` 标记不变 |
| hislip_tls Windows 一次性红 | 不动：main 同时段全绿、重跑转绿；两连红再立案（PR #1701 先例） |
| 满载模拟 | 4 个 `yes` 进程负载下 `test_shared_code_runs_everything` 绿 |
| 行为变化 | 无产品行为变化；`select()` 对外部唯一可观察差异=同进程两次调用间库文件变更现在会被看见（此前每次重扫也看见——语义相同，仅成本变化） |
| 自检 | check.sh 退出码见 PR；改动测试 test_select_tests.py/test_unit_budget.py 17 过 |
| Refs | #1716 的 75 秒墙钟预算对账不在本条（是门禁范围/预算值的分层决策，#1716 验收 1(a)/(b) 二选一待定） |
