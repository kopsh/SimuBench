## 两台仪表填同一个真实地址时测试台校验报错（Issue 1589）

| 完成条件 | 自检 |
| --- | --- |
| validate 报错并返回 1 | ✅ `E-ADDRESS-DUPLICATE`，指向后一台的 `/instruments/<名称>/address`，说明与哪台相同；`tests/test_validate.py` |
| 按 VISA 规范化后比较 | ✅ 规范化移到 `models/addresses.py`（`normalize_address`），PyVISA 后端的 `_norm` 与校验共用同一份，校验不会放过后端分不开的地址；用例 `TCPIP0::10.0.0.5::INSTR` 与 `tcpip0::10.0.0.5::inst0::instr` 判为相同；不同地址、没写地址不报 |
| 画布编辑（`set_address`）有提示 | ✅ 编辑照常保存，返回的 `diagnostics` 带这条错误（问题面板显示）；示例、validation、acceptance、tests 下的测试台全部重跑无新增 |
