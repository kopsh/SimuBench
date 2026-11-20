## 压缩永不丢弃对话首轮：任务起点与用户要求记住的内容保得住（Issue 1708）

| 完成条件 | 自检 |
| --- | --- |
| `compact()` 丢弃循环改为从第二轮起落：保留首轮 + 最近 KEEP_TURNS 轮，首轮永不越过 | ✅ `server/assistant/compaction.py`（break 条件 KEEP_TURNS+1、切割点 `[:starts[1]] + [starts[2]:]`）；清工具结果的路径不变 |
| 压缩后首条 UserPromptPart 仍是首轮原文；DROPPED 提示仍在前 | ✅ `tests/test_assistant.py::test_compaction_keeps_recent_turns_and_drops_whole_old_ones` 补断言 `out[0].parts[1].content == "q0"`（旧代码上失败：首轮被丢后 parts[1] 是更晚的轮）；流式长对话压缩测试不变通过 |
| 窗口极小压不达标时保真优先 | ✅ 循环有界 break，兜底清结果路径照旧（estimate 断言仍过） |
| 背景 | ✅ 复杂 E2E 实测：context 1500 下「记住 EXP-42」随首轮被压缩丢弃，模型如实告知未编造 |
