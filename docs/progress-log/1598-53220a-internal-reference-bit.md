## 53220A/53230A：OPERation「Internal Reference」位有了来源，位号对照 Figure 8-1 的图改正（Issue 1598）

| 完成条件 | 自检 |
| --- | --- |
| `reference_clock` 提供 `INTREF` 标志，OPER 位随 `:SENS:ROSC:SOUR INT\|EXT` 变化，两个型号 | ✅ `reference_clock.status_flags` 新增 `INTREF`：内部振荡器在用时为真（选了内部，默认；或选了外部但没有锁定）；两个定义的 OPER 位表用它 |
| 对照 PDF 图确认位 8 的含义与极性 | ✅ 拉取 `53220-90001.pdf`（LFS），渲染第 278 页 Figure 8-1：**「Internal Reference」是位 9（值 512），位 8 没有标签**——issue 与原定义的「位 8」是错的。图上的 OPER 位：0 Calibrating、1 Running on Battery、2 Battery Critical、4 Measuring、5 Waiting for Trig、9 Internal Reference、10 Instrument Locked、11 Config Chg、12 Rdg Mem Threshold、13 Global Error。原定义的 `INTREF…ERR` 整体偏了一位（8–12），两个型号都改为 9–13；QUES 位表逐位对过图，与定义一致（0、2、5、6、8、10、11、12、14，位 9 Totalize 是事件位）。issue 里「未能核实」的 OPER 其余位与 QUES 位 2/5/6/9 现在都核实了 |
| 用例覆盖 INT 与 EXT 两种状态的 `STAT:OPER:COND?`，更新 `*RST` 后期望值；覆盖表 note 同步 | ✅ 两个型号的手册用例：`*RST` 后 512、`INIT` 后 560（512+48）、`*TRG` 后 512，外部参考选了但失锁时 512；外部参考锁定（REF_IN 上 10 MHz）时位 9 清零、失锁后回到 512 且 QUES 报 1024，在 `tests/test_keysight_53220a.py::test_the_internal_reference_bit_is_set_unless_an_external_reference_locks`（手册用例的测试台没有 REF_IN 信号）；两个型号的覆盖表 OPERation 条件说明与 `docs/component-map.md` 同步 |
| 假设 | 手册没有写外部参考选了但失锁时用哪个振荡器：取内部（位 9 置位，同时 QUES 位 10 报时基错误），覆盖表注明；事件寄存器里位 9 在 `*RST` 时就为真，算基线，不算一次上升沿，`STAT:OPER:EVEN?` 仍是 48 |
