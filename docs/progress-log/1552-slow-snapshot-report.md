## main 的慢测试任务失败时也上传快照差异报告（Issue 1552）

| 完成条件 | 自检 |
| --- | --- |
| main 变绿 | main（76d06bd）的慢测试任务红在 `test_nets_snapshot[en-…-100x32]`：快照不一致。本地连跑 3 次通过，main 前三次运行都绿，#1550 未改 TUI；按规则重跑一次失败任务 |
| 下次能看到差异 | ✅ `slow` 任务失败时上传 `snapshot_report.html`（与 PR 上的 `tui` 任务相同）。这次日志只有 `assert False`、没有报告，无法确认是哪一处不同 |
