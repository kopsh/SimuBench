## 引擎：bool 设置收 "false"/"OFF" 字符串按解析器词法解析，不再被当成 true 吞掉（Issue 1677）

| 完成条件 | 自检 |
| --- | --- |
| bool 路径收 SCPI 词形（ON/OFF/1/0，大小写不敏感）与 JSON 自然词形 TRUE/FALSE；布尔与数字行为不变 | ✅ `tests/test_value_fit.py::test_a_bool_takes_the_parser_words_and_refuses_other_strings`（11 个词形 + 5 个类型断言）；实现在 `engine/settings.py`，与 enum 的「词形都收」先例（#625）同款 |
| 其余字符串与类型可见报错（-224 带期望词形 / -104 带实际值），不再静默 | ✅ 同上测试的两条 raises 断言；`apply_writes` 先验证后应用，拒绝的写入不落状态 |
| 事务层钉住：字符串写 "false" 生效、"yes" 拒绝且状态与事件不变 | ✅ `tests/test_write_tx.py::test_a_bool_write_takes_the_parser_words_and_refuses_other_strings`（旧代码上失败：旧词法下 "false"→True 落进无变化跳过路径） |
| 修复背景 | ✅ 排查记录见 #1677：GLM flash 工具实参 `value:"false"`（字符串）→ `bool("false")=True` → 无变化静默跳过 → 助手重试 3 次被 stuck 护栏止损；SCPI 层 `parse_bool`（values.py）词集一致 |
