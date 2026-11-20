## 停止同意「只活一轮」收成 Conversation 上的一条判据（Issue 1726）

| 完成条件 | 自检 |
| --- | --- |
| 判据单点：`Conversation.stop_consent_alive(turn)` 是唯一的「紧邻下一轮有效」公式；发送侧的 stale 遗忘收进 `Conversation.stop_consent_pending()`（判据上只做查询不减条件），service.py 不再触碰 `stop_requested_in` | `grep -rn stop_requested_in src/`：只剩 store.py 的字段/持久化/判据与 tools.py 的记录写入（ask 记录、停完清空） |
| service.send 与 tools.stop_session 各一次判据调用，行为零变化：#1707 的同意转述 note、三条 Forbidden 文案、stale 遗忘时机原样 | `pytest tests/test_assistant.py`：整轮场景 30 条原样存活（含 #1707 note 与「很下一轮才点同意」） |
| 不变量有脱离 ScriptedLLM 整轮循环的单测：判据真值表（无请求无效、同轮无效、紧邻轮有效、隔轮无效）、发送侧 stale 遗忘与保留、stop_session 门全分支（含文字回复不算同意、拒绝后测试台仍在跑、同意即停并清记录、已停再答「已经停止」） | `tests/test_assistant.py::test_stop_consent_is_alive_for_the_next_turn_only`、`::test_sending_answers_one_request_and_forgets_a_stale_one`、`::test_stop_session_gate_asks_the_same_criterion` |
| 不写新 D：轮语义属 feature 级，实选「重构收口、判据进 store 记录类」未过两标准（无可逆的真实备选路径） | 决策条目数不变 |
| check.sh 绿 | `scripts/check.sh` 退出码 0（`test_select_tests.py::test_shared_code_runs_everything` 因本机并行负载偶发超 0.1s CPU 预算，为存量 #1716，单跑即绿，本 PR 不修） |
