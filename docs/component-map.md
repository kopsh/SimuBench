# 类别 × 组件对照(component-map)

状态:S1(#201)首波 12 个类别已补(已有 4 类 + 首波 8 类);本文与 [docs/blocks/](blocks/) 一起构成 D50 的落地与缺口清单 v1 的底稿。伞 issue:#181(类别铺开)。

## 方法

- 每个类别一节:真实框图(功能块、连线、依据来源)→ 每块映射到哪个组件 → **求解层级** → 已有债务。
- **机器可读块清单**:[docs/blocks/](blocks/)`<class>.yaml`(`schema: simubench.blocks/1`),按定义的 `class` 字段(`instrument_class`)对应——架构跟仪表走,UI 模板(`display.template`)只管界面。`tests/test_block_coverage.py` 检查内置定义的组件(含通道组件)覆盖该类别的每一块;已有型号的缺口进该测试的 `EXEMPT` 例外清单,与 `test_model_components.py` 的 `ALLOWED` 同规则:**只能缩短**。不用组件数量作标准,否则会鼓励凑装饰块。
- 一块可接受多个组件类型(如合成块收 `waveform_synth` 与 `function_generator_core`);块清单可写**尚不存在的组件类型**(未来的拆分),测试只对内置定义生效。
- 组件类型名是设计默认;拆分 PR 落地时可随块清单一次提交调整,同时在本文登记。
- **求解层级**:进电路求解(stamp/emf,参与网络求解)还是设置/数据层(只参与设置与数据计算)。**拆分前必须先定**,否则要么块是装饰,要么要改求解器。
- **已有债务**:5522A 与 SDG2042X 的拆分(第二波,缺口清单);偏软件命名的已有组件——`specan` 的 `mixer_limit`、`maxhold_limit`(命名审计,拆分时更名)。

## oscilloscope

框图:输入端口 → 探头 → 垂直通道(耦合、带宽、档位与偏置)→ ADC → 采集(run/stop/single、存储)⇄ 时基、触发 → 测量 → 波形传输。
依据:类别公认架构;与 SDS2104X Plus、DSO-X 2024A 编程手册的功能划分一致(逐台核对随各型号批次)。

| 块 | 组件 | 求解层级 | 债务 |
| --- | --- | --- | --- |
| input_port | `input_port` | 电路(阻抗到地) | — |
| probe | `scope_probe` | 设置/数据(比例换算) | — |
| vertical | `scope_vertical`(旧名 `scope_frontend`,仅为加载保留) | 信号(滤波与档位) | — |
| adc | `scope_adc` | 数据(量化与噪声) | — |
| acquisition | `scope_acquisition` | 数据 | — |
| timebase | `scope_timebase` | 数据 | — |
| trigger | `scope_trigger` | 数据 | — |
| measurements | `scope_measurements` | 数据 | — |
| waveform_transfer | `waveform_transfer` | 数据 | — |

两台内置型号(keysight-dsox2024a、siglent-sds2104xplus)全覆盖,无豁免。

## function_generator

框图:参考 → 合成(标准波相位连续;任意波走表)→ 幅度与偏置 → 输出级;调制在合成之后。
依据:类别公认架构;与 SDG2042X 手册的功能划分一致。

| 块 | 组件 | 求解层级 | 债务 |
| --- | --- | --- | --- |
| reference | `reference_clock` | 数据(基准偏差) | siglent-sdg2042x 无(例外清单) |
| synthesis | `waveform_synth`、`function_generator_core` | 信号(波形流) | — |
| amplitude | `amplitude_level` | 数据(标量) | siglent-sdg2042x 无(例外清单) |
| output | `output_stage` | 电路 | — |
| arb_memory | `arb_table` | 数据 | siglent-sdg2042x 无(例外清单) |
| modulation | `awg_modulator`(尚不存在) | 待拆分时定 | 组件不存在;siglent-sdg2042x 无(例外清单);第二波(缺口清单) |

内置 siglent-sdg2042x 由专用组件 `siglent_sdg` 承担(test_model_components.py 已记账),缺口在例外清单;拆分在第二波。

## multimeter

框图:输入端子(前/后、耦合)→ 信号调理(量程、滤波)→ A/D(NPLC、分辨率)→ 触发 → 读数缓冲;校准存储在旁路。
依据:Truevolt 手册的测量链([metrology-authoring.md](metrology-authoring.md)、D34)。

| 块 | 组件 | 求解层级 | 债务 |
| --- | --- | --- | --- |
| input_terminal | `dmm_input_terminal` | 电路(端子;是否高阻按量程与 `high_z_ranges` 决定) | — |
| signal_conditioning | `dmm_conditioning` | 电路 + 数据(功能、量程、AC 滤波、模拟误差) | — |
| adc | `dmm_adc` | 数据(NPLC 读数时间、分辨率、校准、读数;sampler) | — |
| trigger | `trigger_model` | 数据 | — |
| buffer | `reading_buffer` | 数据 | — |

E1(#190)已拆分:`keysight-34465a` 与 `sb-dmm-1` 由三个组件组成,端子 `out` 经 signal 连线到调理块;`meter.function`/`meter.range` 的校准键仍由 ADC 转发测量上下文,校准存储接口未改。34+15 条金标准消息(读数与虚拟时间)逐条不变;块覆盖例外清单里 34465A 的三项已删除。

## calibrator

框图:设定值与量程生成(DAC、量程放大)→ 输出控制(工作/待机、输出端子);校准存储在旁路。
依据:5522A 手册的工作原理章节。

| 块 | 组件 | 求解层级 | 债务 |
| --- | --- | --- | --- |
| setpoint_and_range | `calibrator_output` | 电路(源)+ 数据 | `calibrator_output` 为整块;拆分未设计(第二波评估,缺口清单) |
| output_control | `calibrator_output` | 设置 | 同上 |

内置 fluke-5522a 全覆盖。该类别没有已设计的拆分,不存在目标组件类型,块清单以现有组件为覆盖者——这是现状记录,不是豁免;拆分设计立项时改写本节。

## smu

框图:源设定(DAC 与源量程)→ V/I 控制环(CV/CC 交叉与限值,D34)→ 输出级(HI/LO、sense、浮地与输出继电器)→ V/I 测量(量程电阻与 A/D)→ 触发/缓冲;扫描与列表是源设定的一种。
依据:SMU 类别公认架构;B2901A 用户指南(B2910-90010,Edition 6,March 2019)第 2 章 2.9 Source/Measure Unit、2.10 Limit/Compliance、2.11 Output and Measurement Ranges 与第 6 章 Function Details。手册没有公开框图,按类别架构并注明。型号 #193。

| 块 | 组件 | 求解层级 | 债务 |
| --- | --- | --- | --- |
| source_dac | `smu_source` | 设置/数据(源功能、电平、源量程) | — |
| control_loop | `output_control_loop` | 电路(V/I 环与限值切换,D34;B2 复用) | — |
| output_stage | `smu_output` | 电路(HI/LO、sense、浮地、输出继电器;逐点更新模式) | — |
| meter | `smu_meter` | 电路(量程电阻)+ 数据(NPLC、A/D;sampler) | — |
| trigger | `trigger_model` | 数据 | — |
| buffer | `reading_buffer` | 数据 | — |

B1(#184)已拆分:`sb-smu-1` 由 `smu_source`、`output_control_loop`、`smu_output`、`smu_meter` 四个组件组成(测量组件仍名 `core`,前面板按 `core.voltage` 等取读数),34 条金标准消息的回复与虚拟时间逐条不变;控制环不引用 SMU 的组件,设定/限值/输出都经参数绑定,电源输出级(B2)直接复用。

## power_supply

框图(每路):设定 DAC → CV/CC 误差放大 → 功率级 → 输出继电器/sense → 回读 ADC;OVP/OCP 保护;多路之间的跟踪与串并联在通道之间,输出时序在触发/列表。
依据:可编程电源类别公认架构;E36300 系列编程指南(E36311-90008,Edition 12,June 2025)第 1 章 Programming Ranges and Output Identifiers、Channels and Channel Lists,第 2 章 `:VOLTage`、`:CURRent`、`:OUTPut`（含 `:OUTPut:PAIR` 串并联与 `:OUTPut:TRACk` 跟踪）、`:PROTection`、`:TRIGger`。手册没有公开框图,按类别架构并注明。型号 #198。

| 块 | 组件 | 求解层级 | 债务 |
| --- | --- | --- | --- |
| setpoint_dac | `power_supply_setpoint`(尚不存在) | 设置/数据(每路电平、量程) | B2(#185)拆出 |
| cv_cc_loop | `output_control_loop` | 电路(CV/CC 交叉;复用 B1 的限值切换环) | B1 已拆出,B2 绑定每路的设定 |
| power_stage | `power_stage`(尚不存在) | 电路 | B2 |
| output_stage | `power_output`(尚不存在) | 电路(继电器、sense) | B2 |
| readback_adc | `supply_readback`(尚不存在) | 数据(电压/电流回读) | B2 |
| protection | `supply_protection`(尚不存在) | 设置/数据(OVP/OCP 锁定) | B2 |
| tracking | `supply_tracking`(尚不存在) | 设置/数据(跟踪/串并联) | B2 |

`DcSupply` 是固定 EMF 的台面配件,不是可编程电源,不计入本类别。

## electronic_load

框图:输入 → 功率级 → 模式环(CC/CV/CR/CP)→ V/I 测量;保护(OVP/OCP/OPP/OTP)。动态/列表(C2)不在首波。
依据:电子负载类别公认架构;DL3000 编程指南(PGJ01106-1110,Oct 2025)第 2 章 `:SOURce:FUNCtion`、`:SOURce:CURRent|VOLTage|RESistance|POWer`、`:SOURce:CURRent:SLEW`、`:SOURce:CURRent:VON`、`:SOURce:INPut`、`:SOURce:OCP|OPP`。手册没有公开框图,按类别架构并注明。型号 #194。

| 块 | 组件 | 求解层级 | 债务 |
| --- | --- | --- | --- |
| input | `eload_input`(尚不存在) | 电路(输入端子与输入开关) | C1(#186)拆出 |
| power_stage | `eload_power_stage`(尚不存在) | 电路 | C1 拆出 |
| mode_loop | `eload_mode_loop`(尚不存在) | 电路(CC/CV/CR;CP 非线性另定) | C1 拆出 |
| meter | `eload_meter`(尚不存在) | 电路(分流/分压)+ 数据 | C1 拆出 |
| protection | `eload_protection`(尚不存在) | 设置/数据(OVP/OCP/OPP/OTP) | C1 拆出 |

现有 `sb-el-1` 用整块 `electronic_load`(在 `smu.py`);C1 先移模块再拆分。**动态/列表(C2)** 记入缺口清单另排;DL3000 手册只有 `VON`(单一启动电压),`Voff` 的回差行为在 #194 中按手册核对。

## spectrum_analyzer

框图:RF 输入(50 Ω)→ 输入衰减器 → 混频器(本振)→ 中频滤波(RBW)→ 对数检波 → 视频滤波(VBW)→ 检波器 → 迹线/标记;参考输入 10 MHz。
依据:频谱仪类别公认架构;N9320B 编程指南(N9320-90029,Edition 5,December 2020)的 `:INPut`、`:SENSe`(频率、带宽、扫描)、`:CALCulate`(标记)、`:TRACe`、`:TRIGger` 命令组与第 2 章 Programming Fundamentals。手册没有公开框图,按类别架构并注明。型号 #196。

| 块 | 组件 | 求解层级 | 债务 |
| --- | --- | --- | --- |
| input_port | `input_port` | 电路(50 Ω 到地) | — |
| input_attenuator | `specan_input`(尚不存在) | 设置/数据(衰减、输入阻抗) | D1(#187)拆出 |
| mixer | `specan_mixer`(尚不存在) | 数据(频率搬移与本振) | D1 拆出 |
| if_filter | `specan_if`(尚不存在) | 数据(RBW 与噪声带宽) | D1 拆出 |
| log_detector | `specan_detector`(尚不存在) | 数据(对数检波) | D1 拆出 |
| vbw_filter | `specan_vbw`(尚不存在) | 数据(VBW) | D1 拆出 |
| trace | `specan_trace`(尚不存在) | 数据(检波器、平均/最大保持、标记) | D1 拆出 |
| reference | `reference_clock` | 数据(10 MHz 参考与偏差) | — |

现有 `sb-sa-1` 用整块 `spectrum_analyzer`;D1 拆分后 validation 定义改用它,金标准与频谱仪验收场景不改通过。

## rf_generator

框图:参考(内部/外部 10 MHz)→ 合成器(PLL/VCO)→ 调制器(AM/FM/PM)→ ALC/电平 → 衰减器 → RF 输出(50 Ω)。
依据:射频信号源类别公认架构;DSG800 编程指南(PGG02104-1110,Sept 2018)第 2 章 `:SOURce`(频率、电平、调制、扫描)、`:OUTPut`、`:TRIGger`、`:UNIT` 命令组。手册没有公开框图,按类别架构并注明。型号 #197。

| 块 | 组件 | 求解层级 | 债务 |
| --- | --- | --- | --- |
| reference | `reference_clock`(现有)或 `rf_reference`(尚不存在) | 数据(时基误差) | D2(#188)拆出 |
| synthesizer | `rf_synthesizer`(尚不存在) | 数据(载波频率) | D2 拆出 |
| modulator | `rf_modulator`(尚不存在) | 数据(AM/FM/PM 与卡森规则) | D2 拆出 |
| level_control | `rf_level_control`(尚不存在) | 数据(电平、ALC) | D2 拆出 |
| attenuator | `rf_attenuator`(尚不存在) | 数据/电路(输出衰减) | D2 拆出 |
| output | `rf_output`(尚不存在) | 电路(50 Ω 输出) | D2 拆出 |

现有 `sb-sg-1` 用整块 `rf_generator_core`;D2 拆分后 validation 定义改用它,金标准与射频源验收场景不改通过。脉冲/任意波调制不由首波块清单承担,记入缺口清单。

## lockin

框图(手册第 3 章功能框图):输入 → 50/60 Hz 陷波 → 低噪声差分放大 → 100/120 Hz 陷波 → 相敏检波(PSD)← 参考 PLL/移相 → 低通 → DC 增益/偏置/扩展 → X/Y;R/θ;参考输入/内部振荡器/SINE OUT/TTL OUT;数据缓冲(SRAT/TRCA)与 AUX。
依据:SR830 手册(Stanford Research Systems,Revision 2.5,10/2011)第 3 章 SR830 Basics 的 functional block diagram(3-5)与 Computer Interface 章。型号 #195。

| 块 | 组件 | 求解层级 | 债务 |
| --- | --- | --- | --- |
| input_filter | `lockin_input_filter`(尚不存在) | 电路(耦合、陷波) | D3(#189)拆出 |
| input_amp | `lockin_input_amp`(尚不存在) | 电路/数据(差分放大与增益) | D3 拆出 |
| mixer | `lockin_mixer`(尚不存在) | 数据(相敏检波) | D3 拆出 |
| reference | `lockin_reference`(现有) | 数据(PLL、移相、内部/外部参考) | — |
| lowpass | `lockin_lowpass`(尚不存在) | 数据(时间常数、斜率、建立) | D3 从 `lockin_demodulator` 拆出 |
| output | `lockin_output`(尚不存在) | 数据(DC 增益/偏置/扩展、X/Y/R/θ) | D3 拆出 |
| buffer | `reading_buffer` | 数据(SRAT/TRCA) | D3 接入 |
| aux | `lockin_aux`(尚不存在) | 电路(AUX 输入输出 4×4) | D3 拆出 |

现有 `sb-lia-1` 用 `lockin_reference` + `lockin_demodulator` 两块;D3 拆分后 validation 定义改用它,金标准与锁相验收场景不改通过。

## daq

框图:三个插槽 → 多路复用卡(继电器、bank/backplane 继电器)→ 内部模拟总线 → 内部 DMM(信号调理 → 放大 → A/D → 处理器)→ 扫描表/触发 → 读数缓冲;数字 I/O 与总加器(34907A)不在首波。
依据:34970A 用户指南(34972-90001,Edition 6,November 2019)第 3 章 System Overview 的整机框图(p.80:earth-referenced/floating、光隔离、模拟总线)与内部 DMM 框图(p.90:signal conditioning → amp → ADC → processor),第 3 章 Module Overview(p.209)与第 4 章扫描。型号 #199。

| 块 | 组件 | 求解层级 | 债务 |
| --- | --- | --- | --- |
| backplane | `daq_backplane`(尚不存在) | 电路/设置(插槽、模拟与数字总线) | E2(#191)拆出 |
| mux_card | `mux_card`(尚不存在) | 电路(继电器闭合改变负载) | E2 拆出 |
| scan_table | `scan_table`(尚不存在) | 数据(通道列表、每通道配置、计数与间隔) | E2 拆出 |
| internal_dmm | E1 拆出的 `dmm_input_terminal` / `dmm_conditioning` / `dmm_adc` | 电路 + 数据 | —（34970A 复用它） |
| trigger | `trigger_model` | 数据 | — |
| buffer | `reading_buffer` | 数据 | — |

首波固定 34970A + 槽 1 的 34901A;可配置插卡、数字 I/O 与总加器(34907A)记入缺口清单,需要时先立文件格式决策。34970A 的「槽位+通道」号域(101–320)是 **#207** 的前置,已在 E2 前解决。

## frequency_counter

框图:输入调理(耦合、阻抗、阈值、低通)→ 门控 → 计数/内插 ← 时基 → 频率/周期/累加/时间间隔 → 统计;两通道各自调理。
依据:53220A/53230A 用户指南(53220-90001,Edition 4,April 2023)第 3 章 Measurements(测量汇总、参考振荡器、频率/周期、时间间隔、累加)、第 4 章 Input Signal Conditioning、第 5 章 Triggering and Gating、第 6 章 Statistics。型号 #200。

| 块 | 组件 | 求解层级 | 债务 |
| --- | --- | --- | --- |
| input | `counter_input`(尚不存在) | 电路(阻抗/耦合)+ 数据(阈值、低通) | F(#192)拆出 |
| gate | `counter_gate`(尚不存在) | 数据(门时间、门控源) | F 拆出 |
| timebase | `counter_timebase`(尚不存在)或 `reference_clock` | 数据(时基与参考) | F 拆出 |
| counter | `counter_core`(尚不存在) | 数据(计数、内插、时间间隔) | F 拆出 |
| statistics | `counter_statistics`(尚不存在) | 数据(均值/最小/最大/标准差) | F 拆出 |
| trigger | `trigger_model` | 数据 | — |

53230A 的无间隙连续测量与时间戳不在首波,记入缺口清单。

## 首波缺口(随类别节记录,第 4 步汇总)

- 电子负载的动态/列表(C2);DL3000 的 `Voff` 回差行为待 #194 按手册核对。
- 射频源的脉冲调制与任意波调制(#197 的未实现范围)。
- DAQ 的可配置插卡、数字 I/O 与总加器(34907A,第二波)。
- 频率计数器的 53230A 独有功能(无间隙测量、时间戳)。
- 现有整块组件的债务:`electronic_load`、`spectrum_analyzer`、`rf_generator_core`、`lockin_demodulator` 由各自的拆分 issue 替换;5522A、SDG2042X 的拆分在第二波。
