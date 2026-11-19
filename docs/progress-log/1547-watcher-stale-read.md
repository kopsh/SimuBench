## 监视器读到旧内容时不覆盖写入点的记录（Issue 1547）

| 完成条件 | 自检 |
| --- | --- |
| main 变绿 | ✅ Python 3.11 又红在 `test_workspace_event_stream_carries_file_and_client_changes`，多一条 `file_changed`。#1529、#1535 之后剩下的窗口：扫描读文件（读到旧内容）期间写入点替换文件并 `note()` 新内容，扫描随后把旧内容记进已知状态、覆盖了 note；下一次 pass 看到新内容与记录不符，又播报一次。改为：读期间 note 落地且读到的不是 note 的内容时，丢弃这次读、保留 note，下一次 pass 按 note 判（note 的占位 stat 保证会再读） |
| 有回归测试 | ✅ `test_watcher_keeps_a_note_that_lands_while_it_reads_the_older_content`（还原修复则红）；原失败用例本地连跑 30 次全过。D85 同步 |
| #1550 上 3.11 同一用例又多一条 `file_changed`（落在会话重启之后） | ✅ 第四个窗口：pass 列完目录后，写入点新建文件（定义「复制为我的仪表」）并 `note()`；pass 结束时该文件在已知状态里却不在本次列表中，被当成「已删除」播报。改为确认文件确实不在磁盘上才算删除。回归 `test_watcher_does_not_take_a_file_created_after_its_listing_for_a_deleted_one`（还原则红）。本地 8 路并发 ×6 轮施压未能复现原失败，按代码推导定位 |
