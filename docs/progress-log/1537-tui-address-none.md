## TUI 消费可空的 address_virtual：空态而非 "None"（Issue 1537）

| 完成条件 | 自检 |
| --- | --- |
| 身份行 | ✅ `instrument.py` 改为 `info.get("address_virtual") or i18n.t("instrument.noScpi")`，`None` 不再进入 `rich.Text`，也不显示 "None" |
| 日志空态 | ✅ `editing.py` 取值后可空时才用 `log.emptyFor` 的地址；无地址时显示同一句「SCPI service is off」 |
| 文案 | ✅ TUI 英文 `instrument.noScpi` 与 Web `workbench.noScpiService` 同文，`check_tui_i18n.py` 的 SHARED 表登记 |
| 测试 | ✅ `tests/test_tui_t2.py::test_without_scpi_the_screen_shows_an_empty_state_not_none`（`address_virtual=None` 打开 smu 屏，断言空态出现且整屏不含 "None"） |
| check.sh | ✅ 全绿 |
