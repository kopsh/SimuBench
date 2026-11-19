## 34465A：MEASure 的电压行先切回电压功能并恢复预置（Issue 1585）

| 完成条件 | 自检 |
| --- | --- |
| `CONF:RES` 后 `MEAS:VOLT:DC?` 读电压而不是超量程 | ✅ 电压两行补 `writes`（功能、量程/分辨率、NPLC、自动调零、触发与采样预置，与 CONFigure 相同），先配置再读；用例读到 0.09930991 V（0.0991 V 经 100 mV 档 100 ppm 增益与 200 µV 零点），`FUNC?` 为 `"VOLT"` |
| 量程/分辨率的关键字与 CONFigure 一致 | ✅ 量程参数写入 `sense.range`（`sets: {sense.range_auto: false}`），`AUTO/MIN/MAX/DEF` 都接受；分辨率参数也写入 `sense.resolution`，直流 `MIN/MAX` 仍取 NPLC 表两端，交流 `MIN/MAX` 回到当前分辨率。当时只覆盖了电压两行：电阻/电流四行的分辨率关键字与 `DEF` 在 #1644 补齐（`DEF` 原先报 −224） |
| 覆盖表同步 | ✅ `:MEASure[:VOLTage][:DC]`、`:MEASure[:VOLTage]:AC` 两条 `implemented` 说明改为「MEASure = CONFigure + READ?」 |
| 回归测试 | ✅ `cases.yaml` 新增一条（功能切换、`AUTO/MIN/MAX/DEF`、量程状态）；校准与噪声测试改用 `READ?` 或显式量程参数，因为 MEASure 现在按手册恢复默认值（10 NPLC、自动量程）；阻抗对照用例在固定量程时把量程传给 MEASure |
