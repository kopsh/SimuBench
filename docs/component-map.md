# 类别 × 组件对照(component-map)

状态:S1(#201)分批补全,本文与 [docs/blocks/](blocks/) 一起构成 D49 的落地与缺口清单 v1 的底稿。伞 issue:#181(类别铺开)。

## 方法

- 每个类别一节:真实框图(功能块、连线、依据来源)→ 每块映射到哪个组件 → **求解层级** → 已有债务。
- **机器可读块清单**:[docs/blocks/](blocks/)`<class>.yaml`(`schema: simubench.blocks/1`),按定义的 `class` 字段(`instrument_class`)对应——架构跟仪表走,UI 模板(`display.template`)只管界面。`tests/test_block_coverage.py` 检查内置定义的组件(含通道组件)覆盖该类别的每一块;已有型号的缺口进该测试的 `EXEMPT` 例外清单,与 `test_model_components.py` 的 `ALLOWED` 同规则:**只能缩短**。不用组件数量作标准,否则会鼓励凑装饰块。
- 一块可接受多个组件类型(如合成块收 `waveform_synth` 与 `function_generator_core`);块清单可写**尚不存在的组件类型**(未来的拆分),测试只对内置定义生效。
- 组件类型名是设计默认;拆分 PR 落地时可随块清单一次提交调整,同时在本文登记。
- **求解层级**:进电路求解(stamp/emf,参与网络求解)还是设置/数据层(只参与设置与数据计算)。**拆分前必须先定**,否则要么块是装饰,要么要改求解器。
- **已有债务**:`dmm_core` 的输入端子/调理/A/D(E1,#190);5522A 与 SDG2042X 的拆分(第二波,缺口清单);偏软件命名的已有组件——`specan` 的 `mixer_limit`、`maxhold_limit`(命名审计,拆分时更名)。

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
| input_terminal | `dmm_input_terminal`(尚不存在) | 电路 | E1(#190)拆出 |
| signal_conditioning | `dmm_conditioning`(尚不存在) | 电路 + 数据 | E1(#190)拆出 |
| adc | `dmm_adc`(尚不存在) | 数据 | E1(#190)拆出 |
| trigger | `trigger_model` | 数据 | — |
| buffer | `reading_buffer` | 数据 | — |

keysight-34465a 的三个缺口在例外清单;E1 落地后更新定义、删除豁免。E1 只拆新块——`trigger_model`、`reading_buffer`、`calibration_store` 已是独立组件,不重做。

## calibrator

框图:设定值与量程生成(DAC、量程放大)→ 输出控制(工作/待机、输出端子);校准存储在旁路。
依据:5522A 手册的工作原理章节。

| 块 | 组件 | 求解层级 | 债务 |
| --- | --- | --- | --- |
| setpoint_and_range | `calibrator_output` | 电路(源)+ 数据 | `calibrator_output` 为整块;拆分未设计(第二波评估,缺口清单) |
| output_control | `calibrator_output` | 设置 | 同上 |

内置 fluke-5522a 全覆盖。该类别没有已设计的拆分,不存在目标组件类型,块清单以现有组件为覆盖者——这是现状记录,不是豁免;拆分设计立项时改写本节。

## 其余类别

smu、可编程电源(power_supply)、电子负载(electronic_load)、频谱仪(spectrum_analyzer)、射频源(rf_generator)、锁相(lockin)、DAQ/开关(daq)、频率计数器(frequency_counter)的节随 S1 分批进:逐台按手册核对框图、依据与求解层级,写入本文件与 [docs/blocks/](blocks/),并回填对应型号 issue(#193–#200)的常用子集与 `*IDN?`。**某类别节合并后,对应拆分 issue(#184、#186、#187、#188、#189、#190、#192)即可开工。**
