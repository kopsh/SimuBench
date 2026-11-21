## 投影与拼写小件桶：字节解码归 encoder、check_verdicts 回表、catalog 拼写归 D60、agent 词汇钉住（Issue 1771）

| 完成条件 | 自检 |
| --- | --- |
| 字节解码收进 encoder 旁：`engine/record.decode_bytes` 公开名（`_bytes` 原文搬入），replay 以 `_bytes = record.decode_bytes` 委托、verdicts 改用公开名——记录字节拼写的写与读同一处 | ✅ `tests/test_verdicts.py`+`test_replay.py` 23 过；`replay._bytes is record.decode_bytes` |
| `mcp_tools.sessions` 的 check_verdicts 并回 D76 表：`except VerdictError: ToolError(str(exc))` 退役（VerdictError 本在 DOMAIN_ERRORS），并入 `except (*DOMAIN_ERRORS, *FILE_ERRORS): fail(exc)` | ✅ `tests/test_mcp.py` 46 过（输出逐字节不变——抛点均单行 f-string，勘探已核） |
| `server/catalog._json` 的非有限数拼写换 `jsonsafe.spelling`（D60 收拢后的漏网点），**NAN 从产出非法 JSON 变正确拼写（行为改进）** | ✅ `_json(inf/-inf/nan)` 手验 INF/-INF/NAN；69 过 |
| `projections.session_summary` 的 `scpi_socket` 别名化：读 `i.address_virtual`（views 用同一公式构建），socket 公式两份并一份；`resource_*/address_*` 词汇对应由测试钉住 | ✅ `tests/test_services.py::test_the_session_summary_s_agent_vocabulary_is_the_dto_s_addresses`（恒等+对应四断言）；`test_mcp.py::test_session_summary_is_one_shape_with_the_addresses` 原样过 |
| ty/lint | ✅ 463 diagnostics 无新增；ruff 绿 |
