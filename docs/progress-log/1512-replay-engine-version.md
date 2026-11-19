## 回放比对引擎版本与平台：不同时与定义、插件一样给出警告（Issue 1512）

| 完成条件 | 自检 |
| --- | --- |
| 版本比对 | ✅ `replay._engine_warnings`：记录头的 `engine` 与当前 `__version__` 不同，追加一条警告（措辞与定义、插件的警告同形：差异可能来自引擎版本）；记录头没有 `engine` 的旧记录不报 |
| 平台比对 | ✅ 比操作系统族（`platform.platform()` 的第一个词：Linux / Windows / macOS），不比内核与 libc 版本——D20 只承诺同平台逐字节一致，跨平台在验收容差内一致，同族不同内核不是差异；不同时提示这一点 |
| 只警告 | ✅ 回放照常进行，不拒绝；CLI 与 `/api/runs` 报告读的本来就是 `open_record` 返回的同一个警告列表，没有新增出口 |
| 测试 | ✅ `tests/test_replay.py::test_replay_warns_when_the_engine_version_or_the_platform_differs`：同机同版本无警告 → 改写头部引擎版本报 1 条 → 再改成 Windows 报第 2 条 → 同族不同内核恢复无警告 |
| 文档 | ✅ architecture.md 5.6 回放一句补列警告项；不写 D（沿 D20 与 D36） |
