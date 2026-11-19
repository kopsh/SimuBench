## 工作区监视器不再把服务器自己写的文件重复播报（Issue 1526）

| 完成条件 | 自检 |
| --- | --- |
| main 变绿 | ✅ Python 3.11 任务红在 `test_workspace_event_stream_carries_file_and_client_changes`：多收到一条 `file_changed`。原因：`WorkspaceWatcher._pass` 在读文件前取已知状态快照，写入点在扫描读文件期间 `note()` 了同一内容，扫描随后覆盖并按旧快照比较而重复播报；改为哈希之后在锁内按当前已知状态判断 |
| 有回归测试 | ✅ `test_watcher_stays_silent_when_a_write_point_notes_the_content_mid_pass`（还原修复则红） |
