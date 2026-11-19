## 34465A 示例测试台的说明写对输入阻抗命令（Issue 1590）

| 完成条件 | 自检 |
| --- | --- |
| 修改测试台说明 | ✅ `examples/benches/keysight-34465a.bench.yaml` 的 `description` 改为 `[SENSe:]VOLTage[:DC]:IMPedance:AUTO`，并注明不是 34401A 的 `INPut:IMPedance:AUTO`（实测 `INP:IMP:AUTO?` 回 -113，`VOLT:DC:IMP:AUTO?`、`VOLT:IMP:AUTO?`、`SENS:VOLT:DC:IMP:AUTO?` 都回 0） |
| `grep -rn 'INPut:IMPedance' examples docs tests` 检查其他地方 | ✅ 查过 examples、docs、tests、src、web：其余全是对的——34970A/DAQ970A 的手册就是 `INPut:IMPedance:AUTO`（定义、覆盖表、用例、脚本、示例台说明）；53220A/53230A 的 `:INPut:IMPedance` 是它们自己的输入阻抗命令；N9042B 的 `:INPut:IMPedance:IQ/REFerence` 是频谱仪的；34465A 覆盖表第 492 行正是在说明它不是 `INPut:IMPedance:AUTO`。34465A 上写错的只有这一处说明 |
