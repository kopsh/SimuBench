## 34465A：MEASure 的电阻与电流行分辨率接受 DEF/MIN/MAX 并恢复 CONFigure 预置（Issue 1644）

| 完成条件 | 自检 |
| --- | --- |
| `MEAS:RES?/FRES?/CURR[:DC]?/CURR:AC?` 的分辨率接受 `DEF/MIN/MAX`，复现命令不再报 −224 | ✅ 四条 MEASure 行的 `resolution` 参数补 `keywords`（与电压行相同的写法）：直流两线与四线电阻、直流电流的 `MIN/MAX` 取 NPLC 表两端（100 / 0.02 PLC），交流电流的 `MIN/MAX` 只回到自动分辨率；`DEF` 走参数绑定层的默认值 0（自动）。`tests/models/keysight-34465a/cases.yaml` 新增一条用例覆盖 `MEAS:RES? 1000,DEF`、`MEAS:CURR? 1,DEF`、`MEAS:FRES? DEF,DEF` 与 `MIN/MAX`，`SYST:ERR?` 为 `+0,"No error"`；改动前用例在 `MEAS:RES? 1000,DEF` 上因 −224 无回复而失败 |
| 这些行与电压行一致：恢复 CONFigure 默认预置（NPLC、自动调零、触发/采样） | ✅ 四条 MEASure 行的 `writes` 由只写功能与量程扩为 CONFigure 的完整预置（分辨率 0、10 PLC、自动调零、立即触发、触发/采样计数 1、延时 0 与自动延时），用例断言恢复后的 `VOLT:DC:NPLC?`、`ZERO:AUTO?`、`TRIG:SOUR?`、`TRIG:COUN?`、`SAMP:COUN?` |
| 覆盖表同步 | ✅ `keysight-34465a.coverage.yaml` 的 `MEASure:RESistance`、`MEASure:FRESistance`、`MEASure:CURRent[:DC]`、`MEASure:CURRent:AC` 四条 `implemented` 说明改为「MEASure = CONFigure + READ?」并注明 #1644 |
| 修正进度记录中不实的描述 | ✅ `docs/progress-log/1585-meas-volt-function.md` 原称「其余 MEASure 行的分辨率同样绑定设置，`DEF` 不再报 −224」，改为「#1585 只覆盖电压两行，电阻/电流四行在 #1644 补齐」 |
