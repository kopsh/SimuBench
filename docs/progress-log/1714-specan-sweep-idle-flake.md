## 修复 main 红：单次扫描测试在 Windows 上被空闲墙钟时间污染（Issue 1714）

| 完成条件 | 自检 |
| --- | --- |
| `test_single_sweep_takes_its_time` 不再依赖机器速度（命令时钟默认 `idle: wall` 会把命令间 ≥10 ms 的空闲计入虚拟时间，Windows 调度抖动多算 15 ms） | `acceptance/test_specan_scenario.py` 以 `idle="off"` 打开 bench；`uv run pytest acceptance/test_specan_scenario.py` |
