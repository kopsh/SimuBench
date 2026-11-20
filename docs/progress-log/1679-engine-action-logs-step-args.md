## 可观测性：引擎 configure/panel 动作进 INFO 日志，助手 step 带工具实参（Issue 1679）

| 完成条件 | 自检 |
| --- | --- |
| `Session.configure` / `Session.panel` 各一条 INFO（仪表、路径/动作、值、client） | ✅ `engine/session.py` 两条 `log.info`，值用 `%r`（字符串实参原样可见，#1677 排查要的那一行）；日志环与 `--log-file` 均走 root handler，无需 ops 改动 |
| `AssistantStep.args`（工具收到的实参，紧凑键），随轮记录与 step_done 事件走 | ✅ `contracts/api.py` 可选字段；`tools.py` 的 `_call`/`_call_async` 唯二盖章点（`_Step.stamp`，只盖工具自身 step，edit 子步骤不带）；schemas 与 web 生成类型同步 |
| 测试 | ✅ `tests/test_session_command.py::test_a_configure_and_a_panel_action_leave_one_info_line_each`（caplog 两条断言）；`tests/test_assistant.py` 流程测试补 `step.args == {"i","p","v"}` 断言 |
| 背景 | ✅ #1677 排查：日志环里引擎动作零痕迹、LLM 实参只能从 PydanticAI 存档挖；bench 历史/存档/护栏绕路破案，现场第一手信息缺失 |
