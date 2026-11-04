# 类别 × 组件对照(component-map)

状态:S1(#201)首波 12 个类别已补(已有 4 类 + 首波 8 类);本文与 [docs/blocks/](blocks/) 一起构成 D50 的落地。每类块表的「实现状态」列与文末的缺口清单 v1 在 #309 定稿。伞 issue:#181(类别铺开)。

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

| 块 | 组件 | 求解层级 | 实现状态 | 债务 |
| --- | --- | --- | --- | --- |
| input_port | `input_port` | 电路(阻抗到地) | 已实现 | — |
| probe | `scope_probe` | 设置/数据(比例换算) | 已实现 | — |
| vertical | `scope_vertical`(旧名 `scope_frontend`,仅为加载保留) | 信号(滤波与档位) | 已实现 | — |
| adc | `scope_adc` | 数据(量化与噪声) | 已实现 | — |
| acquisition | `scope_acquisition` | 数据 | 已实现 | — |
| timebase | `scope_timebase` | 数据 | 已实现 | — |
| trigger | `scope_trigger` | 数据(每个信源各自的电平 `level_by_source`,#248) | 已实现 | — |
| measurements | `scope_measurements` | 数据 | 已实现(VPP…PERiod 与形状测量:顶/底、过冲/预冲、上升/下降、脉宽、占空比、边沿与脉冲计数,#248;相对触发点的时间测量 XMAX/XMIN/VTIMe/TEDGe/TVALue、位速率 BRATe、两源 DELay/PHASe,触发电平 ASETup,#350;屏幕测量列表:不带 ? 的测量上屏、槽位上限 `slots`、RESults? 带统计、CLEar、ALL? 快照,#373) | 分段存储未建模;测量窗口只用主记录(没有缩放记录) |
| waveform_transfer | `waveform_transfer` | 数据 | 已实现 | — |

两台内置型号(keysight-dsox2024a、siglent-sds2104xplus)全覆盖,无豁免。

## function_generator

框图:参考 → 合成(标准波相位连续;任意波走表)→ 幅度与偏置 → 输出级;调制在合成之后。
依据:类别公认架构;与 SDG2042X 手册的功能划分一致。

| 块 | 组件 | 求解层级 | 实现状态 | 债务 |
| --- | --- | --- | --- | --- |
| reference | `reference_clock` | 数据(基准偏差) | 已实现 | siglent-sdg2042x 无(例外清单) |
| synthesis | `waveform_synth`、`function_generator_core` | 信号(波形流) | 已实现(按波形的最高频率 `max_frequency`:FREQ? MAX 随波形、超限置为上限;脉冲的线性边沿 `lead_time`/`trail_time` 与 `pulse_width`/`pulse_period`/`pulse_edges` 操作,#437) | 按幅度分档的频率上限未建模 |
| amplitude | `amplitude_level` | 数据(标量) | 已实现(高/低电平操作 `high`/`low`：设一个保持另一个，改写幅度与偏置，被拒绝时两者都不变，#424、#428；`load_change`：改负载设定时保持设定值或保持实际输出，#436) | siglent-sdg2042x 无(例外清单) |
| output | `output_stage` | 电路 | 已实现 | — |
| arb_memory | `arb_table` | 数据 | 已实现(易失存储：按名装载 `load`/`load_dac`、`select`、`catalog`、`points`、`clear`；`waveform_synth` 的 `arb`/`prbs` 引用播放它，#426；名字长度 `max_name` 与重名 `exists_error`、`*RST` 回到默认波形而保留存储，#430) | siglent-sdg2042x 无(例外清单) |
| modulation | `awg_modulator` | 信号(合成之后、幅度与偏置之前:AM/FM/PM/FSK/PWM,内部正弦或外部输入调制,总开关;#327) | 已实现(`sb-awg-2` 用上) | 调制波形只有正弦;BPSK/ASK/Sum/扫频未建模。谱线超出 `line_budget`(默认 2000)时:FM/PM 拒绝(-222,展开前按 Carson 规则计数,#357);PWM 保留预算内的低次谐波、截掉其余并附 `truncated` 保真度提示(写明保留次数;预算内谐波全保留、不附提示,#440),常规基频(如 1 kHz 方波,300 MHz 带宽)照常输出(#388)。SDG2042X 的专用组件只承担 BSWV 方言,没有调制,无需迁移 |

内置 siglent-sdg2042x 由专用组件 `siglent_sdg` 承担(test_model_components.py 已记账),缺口在例外清单;拆分在第二波。

## multimeter

框图:输入端子(前/后、耦合)→ 信号调理(量程、滤波)→ A/D(NPLC、分辨率)→ 触发 → 读数缓冲;校准存储在旁路。
依据:Truevolt 手册的测量链([metrology-authoring.md](metrology-authoring.md)、D34)。

| 块 | 组件 | 求解层级 | 实现状态 | 债务 |
| --- | --- | --- | --- | --- |
| input_terminal | `dmm_input_terminal` | 电路(端子;是否高阻按量程与 `high_z_ranges` 决定;`mode` 电阻送按量程的测试电流、四线读 SENSE 端子,电流并分流电阻,#301) | 已实现 | — |
| signal_conditioning | `dmm_conditioning` | 电路 + 数据(功能、量程、AC 滤波、模拟误差) | 已实现(RES/FRES/DCI/ACI 功能与各自量程 `range_res`/`range_curr`,#301) | — |
| adc | `dmm_adc` | 数据(NPLC 读数时间、分辨率、校准、读数;sampler) | 已实现 | — |
| trigger | `trigger_model` | 数据 | 已实现 | — |
| buffer | `reading_buffer` | 数据 | 已实现 | — |

E1(#190)已拆分:`keysight-34465a` 与 `sb-dmm-1` 由三个组件组成,端子 `out` 经 signal 连线到调理块;`meter.function`/`meter.range` 的校准键仍由 ADC 转发测量上下文,校准存储接口未改。34+15 条金标准消息(读数与虚拟时间)逐条不变;块覆盖例外清单里 34465A 的三项已删除。

## calibrator

框图:设定值与量程生成(DAC、量程放大)→ 输出控制(工作/待机、输出端子);校准存储在旁路。
依据:5522A 手册的工作原理章节。

| 块 | 组件 | 求解层级 | 实现状态 | 债务 |
| --- | --- | --- | --- | --- |
| setpoint_and_range | `calibrator_setpoint` | 信号(功能 DCV/ACV/DCI/ACI/OHM、电平、频率、建立过渡与隐藏误差;量程表、RANGE? 与量程锁定,量程/极性改变的附加建立时间,#552;电流与电阻输出,电阻取最近标称值,#550) | 已实现(#329) | 整块的 `calibrator_output` 保留给拆分前的定义(test-calibrator),行为与两块组合相同 |
| output_control | `calibrator_control` | 电路(工作/待机断开端子、`z_out`、过载跳闸、`standby_above`/`standby_rule`/`standby_on_function`(true 或 above,#566)回待机规则 #553、#558;外部 sense:SENSE 端子与允许的量程,#551;电流输出为电流源、超 compliance 跳闸,电阻输出为端子间电阻,#550) | 已实现(#329;5522A 已迁移) | 只有一个输出端子:5730A 的宽带输出(Wideband)与外部放大器接口要另一个端子与选择,需要时另开核心 issue |

5730A 与 5522A 的远程方言(#329 的评估,未按 5730A 手册正文核实):两者同属 Fluke 校准器命令集——`OUT <值>[,<频率>]`、`OPER`/`STBY`、`ERR?`、`*OPC?` 与按单位分派的参数,拆分后的两块与已有的 `quantities` 参数足以表达;差别在输出端:5730A 有宽带输出选件与 5725A 放大器接口(另一套端子与选择命令),以及更细的量程结构。前者需要输出控制块支持多个端子(上表),后者只是定义里的量程表。型号 issue(#341)开工时按手册核对这些命令,确需平台能力再开核心 issue。

内置 fluke-5522a 全覆盖。该类别没有已设计的拆分,不存在目标组件类型,块清单以现有组件为覆盖者——这是现状记录,不是豁免;拆分设计立项时改写本节。

## smu

框图:源设定(DAC 与源量程)→ V/I 控制环(CV/CC 交叉与限值,D34)→ 输出级(HI/LO、sense、浮地与输出继电器)→ V/I 测量(量程电阻与 A/D)→ 触发/缓冲;扫描与列表是源设定的一种。
依据:SMU 类别公认架构;B2901A 用户指南(B2910-90010,Edition 6,March 2019)第 2 章 2.9 Source/Measure Unit、2.10 Limit/Compliance、2.11 Output and Measurement Ranges 与第 6 章 Function Details。手册没有公开框图,按类别架构并注明。型号 #193。

| 块 | 组件 | 求解层级 | 实现状态 | 债务 |
| --- | --- | --- | --- | --- |
| source_dac | `smu_source` | 设置/数据(源功能、电平、源量程) | 已实现(扫描:线性/对数/列表、电压或电流,B2900 的 FIXed/SWEep 词、`sweep_spacing`/`sweep_direction`/`sweep_stair`、按功能的起止与列表 `list_voltage/current` 与 `list`/`list_append`/`list_points` 操作,#398) | 扫描量程模式(SWEep:RANGing)只保存 |
| control_loop | `output_control_loop` | 电路(V/I 环与限值切换,D34;B2 复用) | 已实现 | — |
| output_stage | `smu_output` | 电路(HI/LO、sense、浮地、输出继电器;逐点更新模式) | 已实现 | — |
| meter | `smu_meter` | 电路(量程电阻)+ 数据(NPLC、A/D;sampler) | 已实现(测量功能 #299;`function` 只认 VOLTage/CURRent/RESistance,绑定的状态须为这些取值的枚举,#308;读数带 SOUR 元素即源输出设定值,#252;测量功能使能 `measure_voltage/current/resistance` 与 `function_on/off/all/count/state` 操作,B2900 的 `:SENS:FUNC[:ON]/OFF`,#397,未使能的功能仍出读数) | 按功能分开的测量设置未建模(见下) |
| trigger | `trigger_model` | 数据 | 已实现(单层;多通道时每通道一个,`*TRG` 触发全部在等总线触发的通道,#382) | ARM 层未建模(B2901A 覆盖表) |
| buffer | `reading_buffer` | 数据 | 已实现 | — |

B1(#184)已拆分:`sb-smu-1` 由 `smu_source`、`output_control_loop`、`smu_output`、`smu_meter` 四个组件组成(测量组件仍名 `core`,前面板按 `core.voltage` 等取读数),34 条金标准消息的回复与虚拟时间逐条不变;控制环不引用 SMU 的组件,设定/限值/输出都经参数绑定,电源输出级(B2)直接复用。`smu_meter` 的 `function` 参数(#299)可选 VOLTage / CURRent / RESistance,把读数变成测量功能选的量(V/I/V÷I,无电流时为过载值);不绑定(默认 None)时仍是原来的“强制量”,`sb-smu-1` 与 B2901A 的金标准不变。

多通道(#382):整条 SMU 链(`smu_source`、`output_control_loop`、`smu_output`、`smu_meter`、`trigger_model`、`reading_buffer`)可以作为 `channel_components` 每通道一条;组件引用先解析到本通道(`Component.peer`),验证仪表 `sb-smu-2` 与 `tests/test_smu_channels.py` 覆盖两路各自源/测、通道列表顺序、单路限值与 `*TRG`。B2912B(#330)据此只写 YAML。

测量功能的两点结论(#308):`:MEASure:<function>?` 改变当前测量功能的副作用,由命令的 `writes` 表达(操作命令执行前先写设置,如 `writes: {sense.function: CURRent}`);按功能分开的测量设置(各功能各自的 NPLC 与自动量程,2450 的写法)未建模,`smu_meter` 的 `nplc` 与 `measure_autorange` 对所有功能共用一份。Keithley 2450(#12)开工时若需要按功能记忆,另开核心 issue 加按功能的设置组。

## power_supply

框图(每路):设定 DAC → CV/CC 误差放大 → 功率级 → 输出继电器/sense → 回读 ADC;OVP/OCP 保护;多路之间的跟踪与串并联在通道之间,输出时序在触发/列表。
依据:可编程电源类别公认架构;E36300 系列编程指南(E36311-90008,Edition 12,June 2025)第 1 章 Programming Ranges and Output Identifiers、Channels and Channel Lists,第 2 章 `:VOLTage`、`:CURRent`、`:OUTPut`（含 `:OUTPut:PAIR` 串并联与 `:OUTPut:TRACk` 跟踪）、`:PROTection`、`:TRIGger`。手册没有公开框图,按类别架构并注明。型号 #198。

| 块 | 组件 | 求解层级 | 实现状态 | 债务 |
| --- | --- | --- | --- | --- |
| setpoint_dac | `power_supply_setpoint` | 设置/数据(每路电压/电流与量程上限) | 已实现 | — |
| cv_cc_loop | `output_control_loop` | 电路(CV/CC 交叉;复用 B1 的限值切换环) | 已实现 | — |
| power_stage | `power_stage` | 电路/数据(v_max、i_max、p_max 包络;超过 p_max 时 `power_output` 按恒功率工作,回报 CP,#533) | 已实现 | — |
| output_stage | `power_output` | 电路(继电器,动作时间计入虚拟时间;开/关时序延时 rise_delay/fall_delay,#540;sense;输出阻抗) | 已实现 | — |
| readback_adc | `supply_readback` | 数据(电压/电流/功率回读与 CV/CC 状态;`condition` 操作按 0/1/2 回读,#271) | 已实现 | — |
| protection | `supply_protection` | 设置/数据(OVP/OCP 阈值、锁定与恢复;OCP 按 CC 持续时间跳闸,延迟起点 SCHange/CCTRans;按种类查询与清除,#271) | 已实现 | 保护只在模式迭代稳定后判断(#271 修复:提高电流限值时不再误报 OVP) |
| tracking | `supply_tracking` | 设置/数据(仅输出 2/3:跟踪、并联、串联) | 已实现:`topology` 取 `internal`(E36312A:串并联由输出 2 输出、输出 3 停用并拒绝编程 +720,跟踪同号)或 `split`(E36311A ±25 V,物理连接靠接线)(#259) | — |

B2(#185)已拆分:`sb-ps-1` 是三路电源(CH1 +6 V/5 A、CH2 +25 V/1 A、CH3 −25 V/1 A)。每路:设定 → B1 的控制环(CV,电流限值触发 CC)→ 功率级包络 → 输出继电器/sense(0.01 s 动作时间,回读 ≈ 0.5 mV 的输出阻抗压降)→ 回读与 OVP/OCP 锁定;锁定后用型号的 729 拒绝开输出,清除恢复故障前的输出状态。**组合**在设置层保持两路电平一致:跟踪 `CH3 = −|CH2|`、并联/串联 `CH3 = −CH2`,物理连接由测试台连线表示(并联交叉接负输出、串联共负端);PAIR/TRACK 改变时两路必须关闭。瞬变触发由 `supply_transient`(#541)承担:INIT:TRANsient 后的一个触发(总线、立即或 TRIG:TRAN)把 STEP 模式的电压/电流改为触发电平,等待期间报告 WTG-tran/TRAN-active;N6705C 使用。缺口:列表/任意波、数据记录与校准不在首波;组合时 CH3 单独编程的限制与设备错误码留给 #198。`DcSupply` 是固定 EMF 的台面配件,不是可编程电源,不计入本类别。

## electronic_load

框图:输入 → 功率级 → 模式环(CC/CV/CR/CP)→ V/I 测量;保护(OVP/OCP/OPP/OTP)。动态/列表(C2)不在首波。
依据:电子负载类别公认架构;DL3000 编程指南(PGJ01106-1110,Oct 2025)第 2 章 `:SOURce:FUNCtion`、`:SOURce:CURRent|VOLTage|RESistance|POWer`、`:SOURce:CURRent:SLEW`、`:SOURce:CURRent:VON`、`:SOURce:INPut`、`:SOURce:OCP|OPP`。手册没有公开框图,按类别架构并注明。型号 #194。

| 块 | 组件 | 求解层级 | 实现状态 | 债务 |
| --- | --- | --- | --- | --- |
| input | `eload_input` | 电路(输入端子、开关、输入电容;盖章) | 已实现 | — |
| power_stage | `eload_power_stage` | 电路(r_min 拐点、i_max、p_max 包络) | 已实现 | — |
| mode_loop | `eload_mode_loop` | 电路决策(CC/CV/CR/CP;Von 与 CC 斜率) | 已实现(CP 未收敛时取最后一轮模式的工作点并记 solver 事件:D52、#258) | CP 为迭代解(D52) |
| meter | `eload_meter` | 数据(V/I/P 读数;`load.*` 供前面板;STATus:QUEStionable 条件寄存器,#343) | 已实现 | — |
| protection | `eload_protection` | 设置/数据(OVP/OCP/OPP 阈值与锁定) | 已实现 | OTP 不在 DL3021 命令集(类别能力见缺口清单);型号命令映射已在 #194 |

C1(#186)已拆分:`sb-el-1` 由五个组件组成(测量组件仍名 `load`,面板的 `load.voltage/current/power/regulating` 键不变)。行为:CC 带 r_min 拐点;CV 用 `loop_source` 把输入调在设定电压,源电压低于设定值时开路(负载不能灌电流);CR 为设定电阻;CP 按 D52 用 D34 定点迭代;Von 是 CC/CP 的起始电压;CC 斜率按虚拟时间渐变;slew 0 时瞬时;OVP/OCP/OPP 锁定后输入停止导通,保护清除后可恢复。**动态/列表(C2)** 与 Von 阈值附近的模式抖动(DC 模型无滞回)记入缺口清单另排;DL3000 手册只有 `VON`(单一启动电压),`Voff` 的回差行为在 #194 中按手册核对。

## spectrum_analyzer

框图:RF 输入(50 Ω)→ 输入衰减器 → 混频器(本振)→ 中频滤波(RBW)→ 对数检波 → 视频滤波(VBW)→ 检波器 → 迹线/标记;参考输入 10 MHz。
依据:频谱仪类别公认架构;N9320B 编程指南(N9320-90029,Edition 5,December 2020)的 `:INPut`、`:SENSe`(频率、带宽、扫描)、`:CALCulate`(标记)、`:TRACe`、`:TRIGger` 命令组与第 2 章 Programming Fundamentals。手册没有公开框图,按类别架构并注明。型号 #196。

| 块 | 组件 | 求解层级 | 实现状态 | 债务 |
| --- | --- | --- | --- | --- |
| input_port | `input_port` | 电路(50 Ω 到地) | 已实现 | — |
| input_attenuator | `specan_input` | 数据(衰减、输入阻抗、过载判断) | 已实现 | — |
| mixer | `specan_mixer` | 数据(本振/参考误差、显示频率轴) | 已实现 | 命名审计:`mixer_limit`(缺口清单) |
| if_filter | `specan_if` | 数据(RBW、高斯响应、噪声底) | 已实现 | — |
| log_detector | `specan_detector` | 数据(检波与对数) | 已实现 | — |
| vbw_filter | `specan_vbw` | 数据(VBW) | 已实现 | — |
| trace | `specan_trace` | 数据(扫描时间、RF 历史、迹线模式与标记;按序调用前级) | 已实现(标记组 #274:开关、普通/差值与固定参考、最小与左右峰值、搜索方式、计数器开关,读定义可选的 `marker.*` 设置,不声明时仍是一个常开的普通标记) | 命名审计:`maxhold_limit`(缺口清单) |
| reference | `reference_clock` | 数据(10 MHz 参考与偏差) | 已实现 | — |

D1(#187)已拆分,`sb-sa-1` 迁移到这些组件;只有 `input_port` 在电路层,其余都是数据层(频谱是确定性的数据计算,不经过信号连线)。**设置耦合的归属**:自动衰减在 `specan_input`(参考电平保持在混频器限值,2 dB 步进);自动 RBW 在 `specan_if`(`span/100` 取 1-3-10 梯级);自动 VBW 在 `specan_vbw`(跟随 RBW);自动扫描时间在 `specan_trace`(用 RBW 与 VBW);外部参考误差/锁定在 `specan_mixer` 与 `reference_clock`;过载判断在 `specan_input`。金标准与 validation-specan.md 的验收场景不改通过;`SpectrumScreen` 与 TUI 仪表屏幕的读数/迹线键不变。

## rf_generator

框图:参考(内部/外部 10 MHz)→ 合成器(PLL/VCO)→ 调制器(AM/FM/PM)→ ALC/电平 → 衰减器 → RF 输出(50 Ω)。
依据:射频信号源类别公认架构;DSG800 编程指南(PGG02104-1110,Sept 2018)第 2 章 `:SOURce`(频率、电平、调制、扫描)、`:OUTPut`、`:TRIGger`、`:UNIT` 命令组。手册没有公开框图,按类别架构并注明。型号 #197。

| 块 | 组件 | 求解层级 | 实现状态 | 债务 |
| --- | --- | --- | --- | --- |
| reference | `rf_reference`(`reference_clock` 加 REF_OUT) | 数据(时基误差) | 已实现 | — |
| synthesizer | `rf_synthesizer` | 数据(载波频率、列表与步进扫描;频率/电平/两者同扫,按触发或按驻留在虚拟时间里推进,连续/单次,#291) | 已实现 | 仪表自己的列表扫描行(频率,电平,时间)未建模 |
| modulation_source | `rf_mod_source` | 数据(调制频率,随同一时基;相位约定) | 已实现 | — |
| modulator | `rf_modulator` | 数据(AM/FM/PM 与卡森规则、谱线预算;调制总开关 `enabled`,#290) | 已实现 | — |
| level_control | `rf_level_control` | 数据(电平,ALC 的 dBm→EMF;电平扫描时取合成器当前点的电平,#291) | 已实现 | — |
| attenuator | `rf_attenuator` | 数据(输出衰减;本模型 0 dB) | 已实现(固定 0 dB) | — |
| output | `output_stage`(通用) | 电路(50 Ω 戴维南输出) | 已实现 | 块清单的 `rf_output` 为第二波前置声明 |

D2(#188)已拆分:`sb-sg-1` 按上表落地,金标准(`tests/test_rfgen_migration.py`)与射频源验收场景不改通过。电平是理想的、衰减器固定 0 dB(`validation-rfgen.md`),所以输出级用通用的 `output_stage`(D50:同一行为不另造装饰组件)。脉冲/任意波调制不由首波块清单承担,记入缺口清单。

## lockin

框图(手册第 3 章功能框图):输入 → 50/60 Hz 陷波 → 低噪声差分放大 → 100/120 Hz 陷波 → 相敏检波(PSD)← 参考 PLL/移相 → 低通 → DC 增益/偏置/扩展 → X/Y;R/θ;参考输入/内部振荡器/SINE OUT/TTL OUT;数据缓冲(SRAT/TRCA)与 AUX。
依据:SR830 手册(Stanford Research Systems,Revision 2.5,10/2011)第 3 章 SR830 Basics 的 functional block diagram(3-5)与 Computer Interface 章。型号 #195。

| 块 | 组件 | 求解层级 | 实现状态 | 债务 |
| --- | --- | --- | --- | --- |
| input_config | `lockin_input_mode` | 数据(单端 A、差分 A−B、电流输入:A 的电压经 `current_z` 换成电流再乘 `current_scale`;端子阻抗在前面的 `input_port`;屏蔽接地不建模——台架网络共用一个地,#273) | 已实现 | — |
| input_filter | `lockin_input_filter` | 数据(耦合高通、工频与二次工频陷波) | 已实现 | — |
| input_amp | `lockin_input_amp` | 数据(灵敏度满量程、动态储备(固定倍数,或 `reserve_db` 按灵敏度查表、档间对数插值,#273)、输入限幅、前端噪声密度) | 已实现 | — |
| mixer | `lockin_mixer` | 数据(相敏检波) | 已实现 | — |
| reference | `lockin_reference` | 数据(PLL、移相、内部/外部参考、SINE_OUT;检测频率上限 `f_max` 与超限时钳位谐波;外部参考的零相位沿 `ext_slope`,#273;SINE_OUT 的直流电平 `dc_level`,#407) | 已实现 | — |
| lowpass | `lockin_lowpass` | 数据(时间常数、斜率、闭式建立、329 截断检查) | 部分(SYNC 同步滤波未排期) | — |
| output | `lockin_output` | 数据(X/Y/R 各自的偏置与扩展(R 只作用于显示)、R/θ、过载、自动增益/相位、缓冲采样与曲线的 `trace_value`;电流输入时 `unit_scale` 把读数换成 A;过载与失锁报为状态位,#273) | 已实现 | — |
| buffer | `curve_buffer`(SR830);`trigger_model` + `reading_buffer`(sb-lia-1) | 数据(按采样率或每个触发存多条曲线(`<组件>.<量>`),开始/暂停/续采/复位,单次或循环,0 基 bin 读取:ASCII、IEEE 浮点、SR830 的 16 位格式,#273) | 已实现(GPIB 快速流式传输 FAST/STRD 不建模) | — |
| aux | `lockin_aux` | 电路(AUX 4 路 ADC 输入与 4 路 DAC 输出,出现在测试台上) | 已实现 | — |

数据捕获(SR86x 的 CAPTURE*,#333):`curve_buffer` 增加操作 `sample`(某一 bin 上全部记录的曲线,未写到的 bin 读 0)、`bytes`、`progress`(按块向上取整的 kB)、`capture_state`(进行中/已开始/已绕回三位)、`rate`(实际采样率 Hz)、`get`(按 kB 取交织的单精度小端块,须停止后读),`start` 可带 `fresh`(先清空再开始)。

读数参数表(#383,SR86x 的 OUTP?/SNAP?/SNAPD?):`lockin_output.readings` 由定义给出,第 k 项即序号 k,`{word, source}`;`source` 是本块的 X/Y/R/THETA/XNOISE/YNOISE、`$设置` 或 `<组件>.<量>`(另一块的 `trace_value`:AUX 输入、`lockin_reference` 的 FREQUENCY/DETECT)。操作 `readout` 按序号或词(大写短形式或全写)取 `minimum`–`maximum` 项,在同一时刻求值、按请求顺序逗号拼接;操作参数的列表项可以是 `$设置`(SNAPD? 读四个数据通道的指派)。数据通道的指派与读取(#401):`CDSP j,p` 是带索引参数(`index_words` DAT1–DAT4、`index_numbers`)的设置命令,写 `display.dat{ch}`;`OUTR? j` 写成每个 j 一条带 `when` 的 `readout` 命令(`names: [$display.datN]`,词 DATn 与序号各一条),不在表内的 j 无匹配而记执行错误(EXE)。已知偏差:XNOise 与 YNOise 读同一个前端噪声密度估计(模型不分别估计 X、Y 两路的噪声)。配套:响应格式 `general`(C `%g`)、角度参数的弧度后缀(`RAD`/`MRAD`/`URAD` 换算为度)。

D3(#189)已拆分:`sb-lia-1` 按上表落地,金标准(`tests/test_lockin_migration.py`)与锁相验收场景不改通过。新增能力:输入配置(耦合、陷波、动态储备)、X/Y 偏置与扩展、数据缓冲(SRAT/TRCA 经通用的触发模型与读数缓冲)、AUX 电路端口。

## daq

框图:三个插槽 → 多路复用卡(继电器、bank/backplane 继电器)→ 内部模拟总线 → 内部 DMM(信号调理 → 放大 → A/D → 处理器)→ 扫描表/触发 → 读数缓冲;数字 I/O 与总加器(34907A)不在首波。
依据:34970A 用户指南(34972-90001,Edition 6,November 2019)第 3 章 System Overview 的整机框图(p.80:earth-referenced/floating、光隔离、模拟总线)与内部 DMM 框图(p.90:signal conditioning → amp → ADC → processor),第 3 章 Module Overview(p.209)与第 4 章扫描。型号 #199。

| 块 | 组件 | 求解层级 | 实现状态 | 债务 |
| --- | --- | --- | --- | --- |
| backplane | `daq_backplane` | 设置/数据(插槽与已装模块;SCPI 号域的槽位检查) | 已实现 | — |
| mux_card | `mux_card` | 电路(双线继电器 + 模拟总线与四线检测总线;闭合计入电路求解;电阻的测试电流、电流通道的分流由它加在总线上,#301) | 已实现 | — |
| scan_table | `scan_table` | 数据(通道列表、扫描次数与间隔;sampler) | 已实现(CONFigure/MEASure 的通道列表整表写进扫描表并启动一次扫描,#300;每通道测量配置 `channel_settings`:扫描到一个通道时把它的 `ch<n>.*` 装进内部 DMM 的设定,一个扫描表可混合功能,`CONFigure?` 按通道回答,#365) | — |
| internal_dmm | E1 拆出的 `dmm_input_terminal` / `dmm_conditioning` / `dmm_adc` | 电路 + 数据 | 已实现(电阻/电流:34465A 在自己的端子上,34970A 由 `mux_card` 在模拟总线上加测试电流/分流,`four_wire_offset` 把 n 与 n+10 配成四线,`current_channels` 是电流通道,#301) | —(34970A 复用它) |
| trigger | `trigger_model` | 数据 | 已实现 | — |
| buffer | `reading_buffer` | 数据 | 已实现 | — |

E2(#191)已拆分:`sb-daq-1` 是 34970A + 槽 1 的 34901A(20 通道,号域 101–120)。卡是一个组件:20 路 H/L 端口、内部模拟总线与闭合限制;闭合继电器经触点电阻接总线,总线上的 10 MΩ(取自内部 DMM 端子的输入阻抗)因此加载该通道的被测件。扫描表把列表交给触发模型逐点采样,按 34970A 的规则**升序**扫描,每点先断前一路再闭合本路,读数带 `CHAN` 字段;扫描次数是列表重复次数,间隔是设置(首波的读数时间 = 继电器建立时间 + DMM 读数时间)。首波固定配置;可配置插卡、数字 I/O 与总加器(34907A)、以及 34901A「无扫描表时允许多路同时闭合」的条件行为记入缺口清单。34970A 的「槽位+通道」号域(101–320)是 **#207** 的前置,已在 E2 前解决。

## frequency_counter

框图:输入调理(耦合、阻抗、阈值、低通)→ 门控 → 计数/内插 ← 时基 → 频率/周期/累加/时间间隔 → 统计;两通道各自调理。
依据:53220A/53230A 用户指南(53220-90001,Edition 4,April 2023)第 3 章 Measurements(测量汇总、参考振荡器、频率/周期、时间间隔、累加)、第 4 章 Input Signal Conditioning、第 5 章 Triggering and Gating、第 6 章 Statistics。型号 #200。

| 块 | 组件 | 求解层级 | 实现状态 | 债务 |
| --- | --- | --- | --- | --- |
| input | `counter_input`(每通道一个) | 电路(阻抗 1 MΩ/50 Ω)+ 数据(耦合、阈值、灵敏度) | 已实现 | — |
| gate | `counter_gate` | 数据(门时间、门控源、时基带来的真实门长) | 已实现 | — |
| timebase | `reference_clock` + `counter_timebase` | 数据(内部/外部 10 MHz 时基误差;`counter_timebase` 把真实时刻变成仪表时标,含单次分辨率,并提供无间隙连续门,#328) | 已实现(`sb-counter-1` 用上) | — |
| counter | `counter_core` | 数据(频率、周期、累加、时间间隔;采样器接口给触发模型) | 已实现 | — |
| statistics | `counter_statistics` | 数据(均值/最小/最大/样本标准差) | 已实现 | — |
| trigger | `trigger_model`(现有) | 数据 | 已实现 | — |

F(#192)已建立:`sb-counter-1` 按上表落地(两路 `counter_input`、门控、时基、计数、统计与触发),类别默认面板见 `library/panels.py` 的 `_counter`。计数按主谱线的周期事件建模:阈值穿越的波形用 `sample_rel` 在一个周期上采样并线性内插;非谐波相关的多音信号按最强谱线计数(模型边界)。每个事件加内插器单次分辨率(默认 1 ns,模型值)与触发误差(默认 1 ns)的合成误差,按虚拟时刻与种子确定：频率/周期的相对分辨率随门时间变长而下降(约 √2×单次分辨率/门时间),时间间隔的两个通道误差各自独立抽取。累加按半开区间 [start, end) 计数,恰好落在门末的事件归下一门;门控「外部」表示由触发模型开始这一轮(触发源),门长仍由门时间给出。53230A 的无间隙连续测量与时间戳不在首波,后由 #328、#385 补上。

## 缺口清单 v1(#309 定稿)

首波核对(#181 与各型号 issue)发现的通用缺口,分三部分。每条指向对应 open issue;没有 issue 的标「暂不排期」。第二波选型已在 #317 定稿:12 个类别各补一台旗舰型号(型号 issue #330–#341,新开核心 issue #327–#329),各型号开工前的能力核对结果补回本清单。`not_modeled` 的逐条明细在各型号 `.coverage.yaml`(13 台共 2430 条),本文按主题归并,不重复罗列。

第二波型号与其依赖(选型理由与排期见 #317;依赖的完成状态以下文各条与类别节为准):

| 类别 | 第二波旗舰 | 型号 issue | 依赖的核心 issue | 对应缺口条目 |
| --- | --- | --- | --- | --- |
| smu | Keysight B2912B | #330 | #252(非阻塞) | 二.2、二.21 |
| frequency_counter | Keysight 53230A | #331 | #328(时间戳/无间隙) | 二.20 |
| daq | Keysight DAQ970A | #332 | #300、#301(部分) | 二.1、二.19 |
| lockin | SRS SR865A | #333 | #273(部分) | 二.18 |
| function_generator | Keysight 33622A | #334 | #327(调制) | 二.22 |
| rf_generator | R&S SMA100B | #335 | #290、#291(部分) | 二.17 |
| spectrum_analyzer | Keysight N9042B UXA | #336 | #274(部分) | 二.16 |
| electronic_load | ITECH IT8900A(备选 Chroma 63600) | #337 | #258(CP 用例) | 二.15 |
| oscilloscope | Keysight UXR0334B | #338 | #248(部分) | 二.13 |
| power_supply | Keysight N6705C | #339 | #271(部分) | 二.14 |
| multimeter | Fluke 8588A | #340 | #301(电阻/电流) | 二.12 |
| calibrator | Fluke 5730A | #341 | #329(`calibrator_output` 拆分) | 二.23、calibrator 节 |

### 一、缺的组件(块清单已声明或核对指认,尚不存在)

| 组件 | 对应块 | 现状 | 排期 |
| --- | --- | --- | --- |
| `rf_output` | rf_generator.output | 块清单已声明(与通用 `output_stage` 并列) | 暂不排期 |
| 通用状态寄存器组件 | 跨类别(STATus 的条件/事件/使能与型号位定义) | `status_register`(#248):命名位、条件(组件 `status_flags()`)与事件(`status_event`)、读后清零(整组或单位)、SRS 的按位使能、摘要进 *STB;作为通道组件时只看本通道的标志(#348);跳变过滤 `ptr`/`ntr`(事件 = (~旧 & 新 & PTR) | (旧 & ~新 & NTR),STATus:PRESet 恢复 `preset_ptr` 与 0,#549);DSO-X 的 :TER/:AER/:OPER、SR830 的四个字节(#273)、E36312A 的可疑寄存器组 QUES → QUES:INST → ISUMmary<n>(#348)已用上 | DL3021 的事件寄存器按需接上 |

### 二、not_modeled 暗示的未来能力(按主题归并)

跨类别的平台能力:

1. **按参数选择组件 / 通道列表启动**:已全部落地——通道列表(#183)、倒序范围与重复通道(#260)、示波器按参数选通道(#248)、电源 INST:SEL 选中输出(#295)、DAQ 通道列表选功能与启动扫描(#300)。
2. **读数元素与格式**:`:FORMat:ELEMents:SENSe/SOUR`(#252)、数值应答带 SI 前缀单位后缀与 `%` 后缀(#289)。二进制传输(FORMat:BORDer,多型号)暂不排期。
3. **读数存储器的高级访问**:`DATA:LAST/POINts/REMove`、按通道统计(CALCulate:AVERage)、FETCh/MEASure 的 MAX/MIN(34465A、53220A、34970A、DL3021 覆盖表)。暂不排期。
4. **状态存储与学习串**:`*SAV/*RCL`、`MEMory:STATe`、`:SYSTem:SETup`、`:LRN?`(需要整机设置的序列化)。暂不排期。
5. **文件系统(MMEMory)与导出**(HCOPy、SAVE、屏幕转储):全型号 not_modeled;仿真无文件系统,有意不做,暂不排期。
6. **接口层**(LAN/LXI/USB/串口配置、接口锁定):不在仿真范围,暂不排期。
7. **校准与自检流程**(`*CAL?`、`CAL:SECure`、校准计数):仿真仪表无漂移,流程无意义,暂不排期。
8. **选件/授权改变能力**(LIC 安装改变量程、Option 300/106/115/150):暂不排期。
9. **触发系统扩展**:ARM 层(B2901A)、定时采样(SAMPle:TIMer)、外部触发沿与触发延迟、锁相的扫描暂停/续采由 `curve_buffer` 实现(#273);其余暂不排期。
10. **显示与前面板仿真**(DISPlay:TEXT、SYSTem:KEY、开机消息):界面由 Web/TUI 承担,暂不排期。
11. **真实时钟类查询**(UPTime?、ONTIME?):回复随真实上电时刻而异,暂不排期。

类别内的功能缺口:

12. **DMM**:温度、频率/周期、电容、通断、二极管、比值测量暂不排期(电阻/电流与 34901A 的四线配对已由 #301 实现)。
13. **示波器**:更多自动测量与运行/触发状态寄存器(#248)、Web 屏幕的光标与自动测量(#280);协议解码/串行触发、mask 测试(MTESt)、search、history、分段存储、数字通道(MSO)、内置波形发生器、FFT 暂不排期。
14. **电源**:列表/动态扫描与数据记录(DLOG)暂不排期。
15. **电子负载**:动态/列表(C2);CP 未收敛时的确定行为已按 D52 落定(#258);MAX/MIN 读数、电阻读数、电池测试暂不排期。
16. **频谱仪**:多个独立标记(目前只有标记 1;标记组由 #274、N9320B 复位默认值与设置顺序由 #292 补上(`specan_trace` 的 `center_fit`/`lo_rate`));组件产生的数值应答(标记/迹线/计数器)写死 7 位有效数字、不随定义声明的应答格式(#366);测量应用(ACP/CHP/OBW/TOI/SEMask)、跟踪源、AM/FM 解调暂不排期。
17. **射频源**:带电平与驻留的列表扫描行(DSG800 的 `:SWE:LIST:*`;电平扫描、驻留与连续/单次已由 #291 补上,调制总开关由 #290);平坦度校准表、脉冲/任意波调制暂不排期。
18. **锁相放大器**:SYNC 同步滤波与 FAST 流式传输暂不排期(#273 的曲线缓冲、输入前端与储备表已补上)。SR865A(#333)落地:读数参数表、%g 应答、弧度、序号/词的枚举由 #383、#400、#402、#403 补上,正弦直流电平(SOFF/REFM)由 #407 补上;SR86x 的双参考与斩波器、同步滤波、自动偏置/量程/定标(OAUT/ARNG/ASCL)、比值(CRAT)、相位回绕(手册 ±360000° 回绕到 ±180°,模型只接受 ±180°)、信号强度(ILVL?)、扫描、数据流、FFT 与截屏、硬件触发的捕获(TRIG IN)暂不排期;数据捕获(立即开始的单次/连续)由 `curve_buffer` 的捕获视图(#404、#405)承担。
19. **DAQ**:温度测量、数字 I/O 与总加器(34907A/DAQM907A)、外部扫描推进、监视通道、报警系统暂不排期。DAQ970A(#332)沿用 34970A 的组件链（DAQM901A 槽 1），扫描表查询的定长块（#415）与空表 `(@)`（#418）已补；`ROUT:CLOS:EXCL`、`ROUT:SCAN:SIZE?`、`DATA:LAST?`、FORMat:READing 字段与 DMM 数字化模式暂不排期。
20. **频率计数器**:连续累加(TOTalize)的读数路径、派生测量(单周期/上升/下降/占空比/相位)、频率比暂不排期;无间隙连续测量已由 #328 的 `counter_timebase` 补上,53230A(#331)以 `SENSe:FREQuency:MODE CONTinuous` 用上。时间戳按 53230A 手册是「比例因子 + 按 `TSTamp:RATE` 抽取的相邻间隔」、经触发模型取回:#385 给 `counter_core` 加了测量功能 `TSTamp`(参数 `timestamp_rate`;比例因子 = ⌊f/速率⌋ + 1,读数 0 为比例因子、其后为相邻被打戳沿的间隔,按时基与单次分辨率),括号里的计数 `(200)` 按一个数解析,53230A 的 `CONFigure/MEASure:ARRay:TSTamp` 与 `TSTamp:RATE` 已接上;Option 150 脉冲/突发测量暂不排期。
21. **SMU**:数字量格式与 GPIO、输出滤波器接入电路、程序内存(TSP)、电阻量程暂不排期;测量功能与按功能的源量程已由 #299/#253 合并,#12(Keithley 2450)的依赖就绪。B2912B(#330)以 #382 的每通道 SMU 链落地;两通道合用电流限制(B2900B SCPI Command Reference Table 2-6,如两路都 ≤ 6 V 时 I1 + I2 ≤ 4 A)与脉冲输出(10.5 A 脉冲档)暂不排期。
22. **函数发生器**:非正弦调制波形、BPSK/ASK/Sum 与扫频(`awg_modulator` 已由 #327 补上 AM/FM/PM/FSK/PWM);内置计数器(SDG)、IQ 调制暂不排期。33622A(#334)用上高/低电平(#424)、任意波易失存储与 ARB/PRBS 播放(#426)、bool 映射的调制开关(#429);噪声、突发、扫频/列表、通道耦合/合并/跟踪、按幅度的频率上限暂不排期;改负载时保持输出电压、换算回读值由 `amplitude_level.load_change: output` 承担(#436,SDG2042X 与 sb-awg-2 仍为默认 `setting`:保持设定值)。
23. **校准器(5522A)**:UUT 误差模式、温度/压力/电容/电阻测量模式、双输出、示波器校准选件(-SC600/-SC1100)、非正弦波形与谐波、交流直流偏置(#90 已知边界)、量程符号名查询(回复需按幅值查表)、不确定度查询、ISR/ISCE/ISCR 状态暂不排期;负 dBm 写入已按 D48 修复(#261,幅度下限 1 mVpp 不含 0,见 docs/lowfreq-cal.md)。

### 三、已有债务

- **bug(核心)**:#279(Web 频谱仪)、#280(Web 示波器)、#282/#283(TUI 新类别读数与仪表屏幕)。
- **文档(可认领)**:#269、#293、#310。
- **验收债务**:#313、#314、#315、#316(昨日 #312 合并后遗留的快照与 e2e 口径)。
- **模型边界(暂不排期,类别节已注记)**:Von 阈值附近无滞回(DC 模型);计数器非谐波多音按最强谱线;`trigger_model` 单层(ARM 缺)。
- **命名审计(暂不排期,拆分/重构时一并)**:`specan_mixer` 的 `mixer_limit`、`specan_trace` 的 `maxhold_limit`。
- **架构目标**:#86(D3 第 2 层:计量类仪表型号只写 YAML);5522A、SDG2042X 的拆分评估随第二波进行。
