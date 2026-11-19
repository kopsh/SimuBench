## 8588A：OPERation 的条件寄存器只有位 4，「读数可用」是事件且不会丢（Issue 1599）

| 完成条件 | 自检 |
| --- | --- |
| `:READ?` 之后 `STAT:OPER:EVEN?` 含位 4（16），再读为 0；`ENAB 16` 时 `*STB?` 位 7 置位 | ✅ `trigger_model` 在每次取完读数时推送 `READINGS` 事件，8588A 的事件寄存器 `oper_ev` 用它做位 4；用例：`ENAB 16`、`*CLS`、`:READ?` → `*STB?` 128、`EVEN?` 16、再读 `*STB?` 0 |
| `STAT:OPER:COND?` 只会是 0 或 16；位 5/8/9 只作事件 | ✅ OPER 拆成两个寄存器：`oper` 只管条件（位 4，ARM），`oper_ev` 管事件/使能/汇总（位 4 READINGS、5 WTG、6、8 LOW、9 HIGH、10、11）；`STATus:OPERation[:EVENt]`/`:ENABle` 指向 `oper_ev`，`:CONDition` 指向 `oper`；`STATus:PRESet` 本来就清所有状态寄存器的使能，照样清 OPER 的。手册 Table 10：条件寄存器只实现位 4，事件寄存器只用位 4、5、6、8、9、10、11；位 0 手册写「Not Used」，不再建 |
| 修正 cases.yaml 与覆盖表 note；`latch: false` 若保留，保证事件不丢 | ✅ `latch: false` 保留（避免 5730A 用例保护跳闸，#1570），位 4 事件是推送的，不依赖读时采样，触发回到 IDLE 也不丢；用例的期望值改为：`INIT` 后条件 16、事件 32；`*TRG` 后条件 0、事件 16；下限失败条件 0、事件 272（256+16）；上限 528（512+16）；覆盖表三条 STATus:OPERation 的说明同步，`docs/component-map.md` 写明 `READINGS` 与双寄存器的做法 |
| 为什么不用已有的 `DATA` 事件名 | B2901A/B2912B 的 MEAS 寄存器有名为 `DATA` 的位并带 ptr/ntr 跳变过滤；推送的事件绕过过滤，会改变它们的行为，所以用专用名 `READINGS`，只有写了这个名字的寄存器才收 |
