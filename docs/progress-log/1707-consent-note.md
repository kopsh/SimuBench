## 助手：同意停止生效时用 note 告诉模型，不再让它凭空猜（Issue 1707）

| 完成条件 | 自检 |
| --- | --- |
| `send()` 在紧邻轮 `stop_consent=True` 时设置 `conv.note`（「用户已在面板点击同意停止：本轮可以直接调用 stop_session」），已有 note 追加不覆盖 | ✅ `server/assistant/service.py`（复用 note 机制：拼进 prompt、finally 清空的生命周期不变） |
| 同意文本随本轮 prompt 进模型上下文 | ✅ `tests/test_assistant.py` 既有同意流程测试补断言：脚本 LLM 收到的历史里含该句 |
| 既有语义不变：打字不算同意、资格消耗（非紧邻轮 consent 置空）、时效过期 | ✅ `tests/test_assistant.py` 26 过；门禁 `tools.py` 未动 |
| 背景 | ✅ 复杂 E2E 实测（GLM-5.3-Flash）：consent=true 已记录且门禁会放行，但模型被上下文两次 Forbidden 锚定，不复述就不再尝试工具 |
