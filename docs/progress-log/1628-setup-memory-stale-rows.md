## 覆盖表里过期的 `blocked_by: setup-memory`（Issue 1628）

`grep -rn "blocked_by: setup-memory" src/simubench/library/instruments` 原有 74 行，逐行核对后改 41 行，剩 33 行（含一个表头笔误）。

| 处理 | 行 | 说明 |
| --- | --- | --- |
| 阻塞原因改为 `file-system` | `MMEMory:LOAD/STORe:STATe`（34465A、B2901A、B2912B、DAQ970A、N9042B、N9320B、SMA100B）、33622A 的 `MMEMory:LOAD/STORe:ALL`、DSO-X 与 SDS2104X+ 的 `:RECall:SETup` / `:SAVE:SETup`、UXR 的 `:DISK:SAVE:COMPosite`、DSG815 的 `:MMEMory:DATA:IQ[:LIST]` 与 `:MMEMory:PNAMe:STATe` | 缺的是仪器文件，不是设置存取；与 #1626 对 53220A/53230A 的处理一致。DSG815 三行保留原备注，只改类别 |
| 改为 `model_work`（平台已能做） | 34465A、53220A/53230A、DAQ970A 的 `STATe:RECall:SELect` / `VALid` / `DELete[:ALL]`；B2901A/B2912B 的 `:SYSTem:PRESet`；SDS2104X+ 的 `:SAVE:DEFault`；SDG2042X 的 `Sys_CFG` | `*SAV`/`*RCL` 的 setup_memory 已接入，这些命令只是没接到它上；`:SYSTem:PRESet` 与 `*RST` 的差别按 D92 靠手册逐项做 |
| 保留 `setup-memory` | `*LRN?`/`*LRN`（学习串）、`:SYSTem:SETup`、上电状态与上电恢复（`:SYSTem:PON*`、`MEMory:STATe:RECall:AUTO`、`OUTPut:PON`、`POWer:ON`、`:SYSTem:PRESet:USER*`）、命名状态、`:OUTPut:SAVE/RECall`（通道设置存储）、SR830 的 `SSET`/`RSET` | 确实缺设置块、学习串、上电事件这些语义，这是本表想留下的行 |
| 表头笔误 | B2912B 的 `:SYSTtem:PON`（无备注） | 改为 `:SYSTem:PON`，补备注 |

issue 点名的行：DSO-X 的 `*OPC` 与 B2901A 的 `:MMEMory:MDIRectory`、`:SYSTem:TIME:TIMer:COUNt?` 在 main 上已经不带 `setup-memory`（先前的修改动过），不再需要处理；B2901A/B2912B 的 `:MMEMory:LOAD/STORe:STATe` 与 `:SYSTem:PRESet` 已按上表处理。其余型号没有手册逐条核对，分类依据是命令名与原备注，只动类别明确的行。
