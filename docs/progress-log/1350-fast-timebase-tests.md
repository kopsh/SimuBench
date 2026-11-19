## 快时基触发回归测试补齐 #1331 验收的时基与屏幕帧（Issue 1350）

| 完成条件 | 自检 |
| --- | --- |
| 时基列表至少含 1、2、5、10、20、100、500 ns/div，AUTO 与 TRIGgered 各一遍，断言不变 | ✅ `test_the_hello_square_triggers_on_its_edge_at_every_timebase`：`(1e-9, 2e-9, 5e-9, 1e-8, 2e-8, 1e-7, 5e-7)` × 两种扫描，每一步断言 t=0 处过 0 V、上升、`:TER?` 为 1；全部通过（原先只有 1、5、10、20 ns/div） |
| 新增一例断言屏幕帧（实时运行）在 5 ns/div 下 t=0 落在边沿上 | ✅ `test_the_running_screen_frame_triggers_on_the_edge_too`：经 `acq.compute([1], frame=N)`（Web 屏幕用的实时路径，帧号不改仪表状态），AUTO 与 TRIGgered 各 5 帧，t=0 的采样处过 0 V 且上升 |
| `scripts/check.sh` 通过且不超时间预算 | ✅ 见 PR；两个测试合计约 5 s |
