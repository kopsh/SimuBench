## 工作区监视器不再把服务器自己写的文件重复播报（Issue 1526）

| 完成条件 | 自检 |
| --- | --- |
| main 变绿 | ✅ Python 3.11 任务红在 `test_workspace_event_stream_carries_file_and_client_changes`：多收到一条 `file_changed`。原因：`WorkspaceWatcher._pass` 在读文件前取已知状态快照，写入点在扫描读文件期间 `note()` 了同一内容，扫描随后覆盖并按旧快照比较而重复播报；改为哈希之后在锁内按当前已知状态判断 |
| 有回归测试 | ✅ `test_watcher_stays_silent_when_a_write_point_notes_the_content_mid_pass`（还原修复则红） |
| e2e `bench-open.spec.ts:85` 不再等不到「已载入在其他地方所做的修改」 | ✅ 页面的工作区 socket 首次连上之前服务器发出的 `file_changed` 没人收到，监视器又因写点 `note()` 不再补报，首次连上不重读，于是编辑器永远不知道；重连的 bump 也只动收到过事件的文件。改为：服务器握手前订阅；前端首次连上时若已有查询读过服务器就整体重读，`epoch` 计入每个文件的修订（D85 同步）。单测 `a resync re-reads a bench file that never had an event`、`the first open resyncs only when a query read the server before the socket listened`；该 e2e 本地连跑 3 遍全过 |
| PR 上 3.11 的 `test_fsk_alternates_between_the_carrier_and_the_hop` 超单元预算 | 本地 <5 ms，CI 0.85 s 为 runner 负载尖峰，与改动无关，重跑 |
