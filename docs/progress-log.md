## DAQ 内部 DMM 的电阻/电流与四线配对（Issue 301）

| 完成条件 | 自检 |
| --- | --- |
| 电阻/电流读数由电路求解，不按命令伪造 | ✅ `mux_card` 按内部 DMM 的功能在模拟总线上加测试电流（RES/FRES）或分流电阻（电流），`dmm_input_terminal` 由总线信号读 V/I、V/R；34465A（端子自带激励）不变。`tests/test_keysight_34970a.py` 的两线（含引线与两个继电器触点）、四线、开路与量程过小（过载）、电流（分流与触点在回路里）、配置后扫描两路 |
| 四线配对由定义声明 | ✅ `mux_card.four_wire_offset: 10`（34901A：通道 n 与 n+10），检测总线经 `mux.sense → inp.sense`；`current_channels: [21, 22]`（34901A 的电流通道 121/122，带 I/L 端子）。功能与通道不符（电流不在 21/22、FRES 无配对、电压用电流通道）在扫描开始前报 -221 |
| 34465A 与 34970A 的手册用例、覆盖检查不退化 | ✅ 34970A 覆盖表 CONF/MEAS 的 RES/FRES/CURR[:DC]/CURR:AC 改为实现；手册用例新增两条（读数、通道与功能不符），通道号域用例按手册的 22 通道改用 123 作不存在的号；示例测试台加 1 kΩ（104/114）与电流源（121）；34465A 用例与测试不改通过 |
| 检查 | ✅ `scripts/check.sh` 全绿 |

## SR830 的曲线缓冲、输入前端与储备表（Issue 273 第 2、4、5 节）

| 完成条件 | 自检 |
| --- | --- |
| 曲线缓冲：按 bin 读取、按显示通道选元素、暂停/续采、二进制读取 | ✅ 新通用组件 `curve_buffer`（`library/components/curve.py`）：最多 4 条曲线，各记 `<组件>.<量>`（`lockin_output`/`lockin_aux` 的 `trace_value`），按采样率在虚拟时间采样或每个触发一点，开始/暂停/续采/复位，单次或循环，0 基 bin 读取 ASCII（每点后带逗号）、IEEE 单精度、SR830 的 16 位格式；SR830 的 SRAT/SEND/TRIG/TSTR/STRT/PAUS/REST/SPTS?/TRCA?/TRCB?/TRCL? 实现。`tests/test_srs_sr830.py`（采样时刻与续采、单次存满与循环保留最新、二进制与 ASCII 一致）。FAST/STRD 流式传输不建模（覆盖表说明） |
| 输入前端：差分与电流输入、参考沿 | ✅ 新通用组件 `lockin_input_mode`（A / A−B / 电流：A 经 1 kΩ 到虚地，1 µA ↔ 1 V，`lockin_output.unit_scale` 把读数换成 A）；SR830 的 ISRC 四种取值、新端子 SIG_B；`lockin_reference.ext_slope`（RSLP：TTL 下降沿使参考移 180°）；IGND 只保存回读（台架网络共用一个地，覆盖表写明不建模的决定）。测试：电流读数 1 V / 11.05 kΩ、A−B 为 A 的一半、下降沿 θ 移 180° |
| 储备随灵敏度变化 | ✅ `lockin_input_amp.reserve_db`（按灵敏度查 dB、档间对数插值），SR830 按手册 p.4-8 三列；测试核对表值与插值 |
| 引擎 | ✅ 参数映射 `map` 可用整数设置匹配字符串键（SR830 的 DDEF 显示量是整数设置） |
| 检查 | ✅ `scripts/check.sh` 全绿；SR830 手册用例新增三条，覆盖表相应命令改为实现 |

## 34970A 每通道测量配置（Issue 365）

| 完成条件 | 自检 |
| --- | --- |
| 功能/量程/分辨率/NPLC 按通道保存，扫描时每个通道按自己的功能求解 | ✅ 34970A 的测量配置改为 `channel_state`（function、range、res_range、curr_range、range_auto、resolution、nplc、imp_auto）；`scan_table.channel_settings` 在检查与读取每个通道前把它的值装进内部 DMM 的 `sense.*`，测试电流/分流/四线检测随之切换；扫描的总时间按各通道自己的 NPLC 求和（读数在其间等距）。引擎：带「整表写入设置」通道列表的操作命令（MEASure?）把 `writes` 与参数绑定的通道设置写到列表中的每个通道 |
| 复现步骤读数为电阻与电压各自的值；混合扫描与 `CONF?` 通道列表形式有用例 | ✅ `tests/test_keysight_34970a.py::test_one_scan_mixes_resistance_and_voltage_channels`（两线与四线电阻 + DCV 同一扫描表、NPLC 按通道、`CONF? (@101,102)`）；手册用例新增「电阻、电压、电流混合扫描与 CONF?」；两条旧用例按手册改为按通道的期望（只设 101 的量程不改 102；CONFigure 只改列表中的通道） |
| 覆盖表与 component-map 的「全局」说明同步 | ✅ 覆盖表 CONFigure/SENSe/INPut 的说明改为按通道，新增 `CONFigure?`；component-map 的 scan_table 行去掉「每通道测量配置」债务 |
| 检查 | ✅ `scripts/check.sh` 全绿 |

## 未建模的参数词与 INFinity 的错误码（Issue 360）

| 完成条件 | 自检 |
| --- | --- |
| 同一关键字长短形式同码 | ✅ `scpi/values.py` 的数值关键字加入 INFINITY、NINFINITY（与 INF、NINF 同义）；`tests/test_not_modeled_words.py`：两种形式解析相同，未声明时有界设置上都是 -222 |
| 定义层能拒绝特定词并给原因 | ✅ 设置与参数的 `not_modeled: {词: 原因}`（D53）：写该词报 -224、事件 reason `not_modeled`；34970A 与 34465A 的 `TRIGger:COUNt` 声明 INFinity，`TRIG:COUN INF/INFinity/infinity` 都回 -224，超出范围的数仍是 -222；覆盖表同步；schemas 与 Web API 类型已重新生成 |
| 检查 | ✅ `scripts/check.sh` 全绿 |

## 组件应答的数值按定义格式（Issue 366）

| 完成条件 | 自检 |
| --- | --- |
| 组件应答可随定义格式 | ✅ `Component.reply_number`；`format.readings: format` 时用定义的应答格式（D54）；频谱、计数器、锁相、曲线缓冲、负载/电源回读、射频列表、校准存储、SMU 改用它；默认 `component` 不变（全量测试未改期望通过） |
| N9320B 统一 16 位 | ✅ 定义 `readings: format`：标记 X/Y、迹线 1001 点、RBW 等都回 `+1.0000000000000000E+005` 形式；手册用例两条与 `tests/test_keysight_n9320b.py` 的 RBW 断言按 16 位更新 |
| 同类型号 | ✅ 53220A 采用 `readings: format`（读数与统计 15 位有效数字，Table 7-1）；覆盖表的 13/11 位说明更正 |
| 检查 | ✅ `scripts/check.sh` 全绿；schemas 与 Web API 类型已重新生成 |

## 示波器屏幕测量表的后端测试、Siglent 对照与光标拖动（Issue 356）

| 完成条件 | 自检 |
| --- | --- |
| 后端测试：经 `/screen` 对照 SCPI 测量，覆盖运行中与无记录 | ✅ `tests/test_scope_screen_measurements.py`：DSO-X `:DIGitize` 后测量表逐项等于 `:MEASure:<item>? CHANnel1`、两次读取相同；SDS2104X Plus 单次采集后等于 `:MEASure:SIMPle:VALue?`（三位有效数字内）；运行中按实时画面测量；停止且无记录时无迹线无测量；采集未取的通道不进表；测不出（无信号的频率/周期）为 null |
| 光标交互（核心决定） | ✅ 决定在波形上直接拖动：竖线改时间、横线改电压，读数画在屏幕右上角，滑块保留为键盘途径；`docs/ui-design.md` 已写；e2e 拖动 X2 到中线读 600 µs |
| e2e 容差 | ✅ 测量表比较改为相对误差 < 1e-3（界面 4 位有效数字，注明依据） |
| 检查 | ✅ `scripts/check.sh` 全绿；`instruments.spec.ts` 的示波器屏幕用例与 `visual -g scope` 通过 |

## TUI 射频源/电子负载读数、英文标题与各类快照（Issue 355）

| 完成条件 | 自检 |
| --- | --- |
| 射频源与电子负载不显示组件内部量，整数为整数 | ✅ `rf_generator`：频率、电平、RF/AM/FM/PM/参考标志、`Point n/N`、`Lines n`、β 与被省略功率，再 Carrier/Modulation/Sweep；`electronic_load`：V、I、P、`INPUT`、CC/CV/CR/CP、`UNREG`，再 Settings。面板读数里无单位的整数按整数显示（34970A 的通道号）。`tests/test_tui_snapshots.py::test_the_rf_source_and_load_screens_show_meaningful_readings` 断言读数文字，且屏幕上没有 `synth.point`、`mod.lines` 等 |
| 标题与分组名无中文（D23） | ✅ 仪表框与身份框的标题只用拉丁字母写的 `label`，否则用型号；同一测试断言整屏无 CJK；原有 SMU、计数器、频谱、锁相快照只因标题变化更新 |
| 射频源、电子负载、电源、DAQ 各有 80×24 与 100×32 快照 | ✅ `CATEGORY_SCREENS` 增加 rfgen（sb-sg-1）、eload（DL3021）、psu（E36312A）、daq（34970A）；后三台在示例工作区，测试加第二个服务 |
| 服务描述 34970A | ✅ 修复：`port_pattern` 生成的端口（继电器端子）在 `describe_instrument` 中 KeyError，示例台此前在服务与 Web 中打不开；`Component.port_spec` |
| 检查 | ✅ `scripts/check.sh` 全绿；TUI 慢层串行通过 |

## DSO-X 时间测量与双电平触发（Issue 350）

| 完成条件 | 自检 |
| --- | --- |
| `:TRIGger:LEVel:HIGH/LOW/ASETup` | ✅ HIGH/LOW 每个信源各一个设置、保存回读（欠幅/转换时间触发未建模，覆盖表记为接受）；ASETup 把每个显示通道的边沿触发电平设到波形 50 %（顶/底之间），AC 耦合时为 0 V（`scope_measurements.auto_trigger_levels`） |
| 其余测量名 | ✅ `scope_measurements`：XMAX/XMIN（第一个极值的时刻）、BRATe（1/最窄脉宽）、VTIMe（某时刻的电压）、TEDGe/TVALue（第 n 个边沿/穿越，从记录左端数）、DELay/PHASe（两源第一个上升沿的时间差与相位，不给信源时用 :MEASure:SOURce 的两个）；时间都相对触发点。测量列表（ALL/RESults/CLEar/SHOW/WINDow）拆为 #373，覆盖表链接 |
| 测试与用例 | ✅ `tests/test_scope_time_measurements.py`（RC 台的闭式解：极值时刻、2 kbit/s、正弦在给定时刻的值、过零时刻、RC 相位滞后 atan(ωRC) 的延迟与相位、ASETup 跟随偏置与 AC 耦合）；手册用例新增一条 |
| 检查 | ✅ `scripts/check.sh` 全绿 |

## DSO-X 屏幕测量列表（Issue 373）

| 完成条件 | 自检 |
| --- | --- |
| 列表：加入、上限、清空 | ✅ `scope_measurements.slots`（DSO-X 为 4）：不带 ? 的测量命令把测量（含信源）放上屏幕，同一项再加移到末尾，超出上限挤出最旧的；`:MEASure:CLEar` 清空列表与统计；*RST 同样清空 |
| RESults? 与统计 | ✅ 按列表顺序每项 `<名称>(<通道>),当前,最小,最大,平均,标准差,次数`，统计与查询共用同一份历史；`ALL?` 回 19 个单源测量的当前值（快照，不上屏；顺序为模型约定，覆盖表注明）；SHOW、WINDow 保存回读（无缩放记录，按主记录测量） |
| 测试与用例 | ✅ `tests/test_scope_time_measurements.py` 新增 4 项（上限与挤出、重复加入、统计与清空、ALL 快照、SHOW/WINDow）；手册用例新增一条；覆盖表 ALL/CLEar/RESults 实现、SHOW/WINDow 接受 |
| 检查 | ✅ `scripts/check.sh` 全绿 |

## E36312A 可疑状态寄存器组（Issue 348）

| 完成条件 | 自检 |
| --- | --- |
| 通用的条件/事件/使能与汇总 | ✅ `status_register` 作为通道组件时只取本通道的标志；`supply_readback` 报 CC/CV；E36312A：每路 ISUMmary<n>（位 0 CC、位 1 CV）→ QUES:INST 位 1–3 → QUES 位 13 → 状态字节位 3；`STATus:PRESet` 清全部使能（`status_register.preset`）。7 条命令改为实现 |
| CV→CC 事件、读后清零、使能掩码 | ✅ `tests/test_keysight_e36312a.py` 新增 2 项（事件置位与清零、另一路不受影响、CC→CV 置位 2；掩码逐级汇总、RQS、掩码不含 CC 时链断、PRESet）；手册用例新增一条 |
| CURR:PROT:DEL 的 *RST 值 | ⚠️ 手边没有编程指南原文，50 ms 仍为模型值，覆盖表如实写明待核对 |
| 覆盖表不再引用已关闭的 #271 作为阻塞 | ✅ |
| 检查 | ✅ `scripts/check.sh` 全绿 |

## N9320B 覆盖表矛盾、单位依据与应答位数（Issue 293）

| 完成条件 | 自检 |
| --- | --- |
| 覆盖表同一命令只有一个等级 | ✅ 删去 `:DISPlay:WINDow:TRACe:Y[:SCALe]:RLEVel` 的重复 `not_modeled` 条目（手册 TRACe 必选的拼写是定义 `[:TRACe]` 可选形式的子集，同一条命令）；`coverage.check` 新增「同一命令形式、等级相反」检查：头按可选节点展开（`_forms`）后若两个条目共享一个拼写且等级不同即报矛盾，字面后缀数字（SOURce1/SOURce2）不算同一条；全库 13 台在展开口径下本就只有这一处等级矛盾，规则加上后 `tests/test_real_models.py` 363 项（含新单测）通过 |
| 单位依据 | ✅ `:UNIT:POWer` 备注写明手册单位表 `DBM|DBMV|DBUV|DBUA|V|W|A`（N9320-90029 Ed.5, p.169）与其余单位回 -224、数据恒为 dBm 的原因；线性刻度显示单位若要建模属组件能力，与 #366 一并看 |
| 回复位数差异指向缺口 | ✅ progress-log issue 196 行已记「组件应答 7 位、定义 16 位」；component-map 缺口 16 补该条并指向新开的核心 issue #366 |
| 检查 | ✅ `scripts/check.sh` 全绿 |

## Keysight 53230A 频率计数器（Issue 331）

| 完成条件 | 自检 |
| --- | --- |
| 定义与依据 | ✅ `keysight-53230a.yaml` 从 53220A 出发（同一手册与命令集），`reference` 为 53220A/53230A User's Guide 53220-90001 Edition 3（September 1, 2017），按手册核对 53230A 的差异：门时间 1 µs–1000 s（频率与累加，p.120、Table 5-2）、单次分辨率 20 ps（Tss）、`SYST:LANG` 默认 `"53230A"`；`simubench validate` 无诊断 |
| 无间隙连续测量 | ✅ `[:SENSe]:FREQuency:MODE` AUTO/RECiprocal/CONTinuous（*RST AUTO，CONFigure 不改变），CONTinuous 映射到 `counter_timebase.gap_free`；`tests/test_keysight_53230a.py`：CONT 下各读数 × 跨度之和等于 8 个门内计到的沿数，AUTO 下不记跨度 |
| 时间戳 | ⚠️ 手册语义（比例因子 + 按速率抽取的间隔、经触发模型取回）与 #328 的绝对时刻操作不同，三条命令标未实现，核心能力另开 #385；派生测量与 Option 150 同 53220A 标未实现 |
| 覆盖、用例与示例 | ✅ 覆盖表 实现 46 · 接受 9 · 未实现 143；`tests/models/keysight-53230a/cases.yaml` 24 条全部通过（新增门时间下限、测量方式、Figure 3-1 的无间隙读数、时间戳 -113）；示例台 `examples/benches/keysight-53230a.bench.yaml` 与脚本 `examples/scripts/keysight_53230a_gap_free.py`（Figure 3-1 设置，由连续读数算阿伦偏差）在测试中运行 |
| 组件不改 | ✅ `src/simubench/library/components/` 无改动；`ALLOWED` 与块覆盖例外清单不变 |
| 检查 | ✅ `scripts/check.sh` 全绿 |

## 多通道 SMU：组件引用按通道解析，*TRG 触发全部通道（Issue 382）

| 完成条件 | 自检 |
| --- | --- |
| 引用按通道解析 | ✅ `Component.peer(name)`：通道组件先找 `ch<n>.<名>`、再找仪表级；`smu_output`、`smu_meter`、`output_control_loop` 的包络、`trigger_model` 的采样器与缓冲改用它；静态校验 `E-DEF-COMPONENT-REF` 对通道组件先查本通道再查仪表级 |
| *TRG | ✅ 触发所有带 `bus_trigger` 的组件，不在等总线触发的跳过；没有一个接受时报第一个的错误，单个触发模型行为不变 |
| 双通道验证仪表与测试 | ✅ `validation/instruments/sb-smu-2.yaml`（整条链在 `channel_components`）；`tests/test_smu_channels.py` 8 项：定义无诊断、两路各自 I = V/R、通道列表按书写顺序回复、一路关输出另一路不受影响、限值只在本路跳闸、`*TRG` 两路都触发、只有一路在等时另一路不报错、无人等待时 -211 |
| 单通道不变 | ✅ B2901A、`sb-smu-1`、E36312A 与其余型号的用例与测试不改通过 |
| 检查 | ✅ `scripts/check.sh` 全绿 |
