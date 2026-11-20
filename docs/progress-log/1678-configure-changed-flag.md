## configure 区分「改成成功」与「写入被吞」：changed 标志（Issue 1678）

| 完成条件 | 自检 |
| --- | --- |
| `ConfigureResponse` 增加 `changed`；`drive.configure` 自比写入前后值 | ✅ `contracts/api.py`（带说明字段）、`server/drive.py`（ok 时 `current != old`；被拒恒 false）；schemas/openapi.json 与 web/src/api/generated 同步再生成 |
| 等值写入 ok 但 `changed:false`，API 层可辨 | ✅ `tests/test_server.py` 会话流：True 写入 changed=true，原值重写 changed=false，被拒 changed=false |
| 助手 `set_live_setting` 用 changed 记 live、给模型一句「值本来就是该值，不要重试」；prompt 补一句 | ✅ `tests/test_assistant.py::test_set_live_setting_tells_the_model_when_the_value_was_already_there`（live 空、返回 changed=false + note）；既有流程测试补 changed=true 断言 |
| 背景 | ✅ #1677 排查发现等值写入与成功不可分辨，助手误判「设置没生效」反复重试至 stuck 止损；changed 是让助手不再重试的那块拼图 |
