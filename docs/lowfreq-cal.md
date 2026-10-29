# 低频校准工作台

既有的低频校准主机程序（PyVISA）驱动“参考示波器 + 示波器校准仪”完成低频校准。本工作台用两台虚拟仪器替换真实仪器：主机程序源码不改，只把资源地址换成 SimuBench 打印的地址（D4、D5）。

内容都在 `examples/lowfreq-cal/`，它本身就是一个工作区：

| 文件 | 内容 |
| --- | --- |
| `instruments/lfcal-reference-scope.yaml` | D1 参考示波器：LeCroy 风格的私有命令方言 |
| `instruments/lfcal-scope-calibrator.yaml` | D2 示波器校准仪：4 通道，SCPI 树 |
| `lowfreq_cal.bench.yaml` | D4 工作台预设：校准仪 CH1–CH4 经 1 m 同轴线缆接示波器 C1–C4 |
| `lowfreq_cal_demo.py` | D5 示例脚本：按主机程序的接口契约完成初始化、逐频点采集与回读、收尾 |

D3（私有文本波形回读）由两部分组成：平台组件 `library/components/lecroy.py`（`INSPECT?` 与 `WAVEDESC`、`PAVA?`、`PACU`；记录长度、触发、运行控制、噪声与 ADC 是通用 `scope_acquisition` 在定义中的参数，见 [instrument-authoring.md](instrument-authoring.md) 3.2），以及通用的 `waveform_transfer`（`DATA_ARRAY_1` 的选点与 `%.6e` 文本，定义中配置，见 [instrument-authoring.md](instrument-authoring.md) 3.1）。示波器的设置命令、`TRSE`、`MSIZ`、`WAVEFORM_SETUP` 与校准仪的全部命令都在定义文件里声明（方言、应答模板、`pairs` 参数、参数映射，见 [instrument-authoring.md](instrument-authoring.md) 2）。

## 1. 部署

```bash
simubench serve examples/lowfreq-cal/lowfreq_cal.bench.yaml
#   SCPI  calibrator   TCPIP0::127.0.0.1::5025::SOCKET
#   SCPI  scope        TCPIP0::127.0.0.1::5026::SOCKET
#   HiSLIP calibrator  TCPIP0::127.0.0.1::hislip0,4880::INSTR
#   HiSLIP scope       TCPIP0::127.0.0.1::hislip1,4880::INSTR
```

主机程序按原样连接 `TCPIP0::127.0.0.1::<端口>::SOCKET`，读写结束符 `"\n"`。端口按测试台中仪器的顺序从 `--scpi-base-port`（默认 5025）起编号。不启动 SimuBench 时主机程序照常连接真实仪器，SimuBench 不改动它的任何文件或配置（AC-5）。

示例脚本：

```bash
python examples/lowfreq-cal/lowfreq_cal_demo.py --calibrator TCPIP0::127.0.0.1::5025::SOCKET \
    --scope TCPIP0::127.0.0.1::5026::SOCKET
python examples/lowfreq-cal/lowfreq_cal_demo.py --bench examples/lowfreq-cal/lowfreq_cal.bench.yaml   # 进程内
```

## 2. 型号识别（FR-1）

主机按 `*IDN?` 的型号段（第 2 段）校验。两台仪器的 `*IDN?` 由测试台中实例的 `idn` 给出（新字段，字段与定义的 `identity.idn` 相同：`{manufacturer}`、`{model}`、`{serial}`、`{firmware}`）：

```yaml
instruments:
  scope:
    model: lfcal-reference-scope
    idn: "LECROY,LFCAL-REFSCOPE,{serial},{firmware}"   # 用户自定义：整串照真机的 *IDN? 回复填写
```

`idn` 由用户自定义：主机脚本校验什么（型号段、厂商段或整串），就照所替换真机的 `*IDN?` 回复填写；可以写死整串，也可以保留 `{serial}`、`{firmware}` 等字段（序列号、固件版本也可用实例的 `serial`、`firmware` 单独设定）。不写 `idn` 时用定义中的默认值（预设里的 `LFCAL-REFSCOPE`、`LFCAL-CALIBRATOR` 只是示例值）。同一套定义可以配多份测试台，各自模拟不同型号串的真机。示例脚本的 `--scope-model`、`--calibrator-model` 指定它校验的型号串，与 `idn` 保持一致即可。

## 3. 两台仪器的定义属于哪一类

两份定义按主机程序的接口契约（需求 §5）编写，不是按某一型号编程手册逐条实现的真实型号：没有 `reference`，不进内置仪表库，放在工作区的 `instruments/` 中由定义搜索路径加载（D42）。契约要求“未知命令不得报错中断”，两份定义都设 `unknown_commands: ignore`：定义之外的命令头不进错误队列、不回复，事件历史中记为 `ignored`（通信记录里看得到被忽略的是什么）。已知命令带错误的值（如 `C1:CPL D75`）仍然报错。

## 4. 参考示波器的命令

命令头按 LeCroy 的短格式与长格式都接受（`C1:VDIV` 与 `C1:VOLT_DIV`）；回复带命令头与单位，如 `C1:VDIV 200E-3 V`，`COMM_HEADER OFF` 时只有值，`LONG` 时为长命令头。`*IDN?`、`*OPC?`、`*CLS?`、`SYST:ERR?` 的回复不带命令头，按契约分别为 IDN 串、`1`、`0`、`0,"No error"`。

| 命令 | 实现 |
| --- | --- |
| `*RST`、`*CLS`、`SYST:PRES` | 复位（预置与 `*RST` 相同）、清错误队列与事件状态；可用 `;` 写在一条消息里 |
| `C<n>:TRACE ON\|OFF` | 通道显示；显示的通道每次采集都记录 |
| `RCLK INTERNAL\|EXTERNAL` | 保存，不影响仿真 |
| `C<n>:CPL A1M\|D1M\|D50\|GND` | 输入阻抗（D50 为 50 Ω，其余 1 MΩ ∥ 16 pF）与前端（A1M 为交流耦合，GND 接地），改变电路 |
| `TRSE Edge,SR,C<n>[,HT,…]` | 边沿触发与触发源；其他触发类型回复 -224 |
| `TRMD AUTO\|NORM\|SINGLE\|STOP` | 运行控制，见第 5 节 |
| `ILVD OFF\|ON` | 保存，不影响仿真 |
| `PACU <i>,<参数>,C<n>` | 保存测量槽位；`C<n>:PAVA? AMPL` 等在最新记录上测量 |
| `C<n>:VDIV <值> mV`、`C<n>:OFFSET`、`C<n>:TRLV`、`C<n>:TRSL`、`C<n>:ATTN` | 垂直档位、偏移、触发电平与沿、衰减 |
| `MSIZ <点数>`（`10K`、`2.5MA`、`1M` 都可）、`TDIV <秒>` | 记录长度与时基 |
| `WAVEFORM_SETUP SP,<sp>,NP,<np>,FP,<fp>,SN,<sn>` | 回读的抽点、点数、起始样本、段（可只给其中几项） |
| `C<n>:INSPECT? "DATA_ARRAY_1",FLOAT` | 文本波形回读，见第 6 节；`"WAVEDESC"` 给出点数、采样间隔、水平偏移等描述 |
| `ARM`、`STOP`、`FRTR`、`WAIT`、`*OPC?` | 单次采集、停止、强制触发、等待未完成的单次采集 |

查询：`MSIZ?`、`TDIV?`、`C<n>:VDIV?` 等每条设置命令都可查询；重复设置同一个值不报错（FR-3）。

## 5. 采集与运行控制

- 记录长度恰为 `MSIZ` 点，覆盖 10 格：采样率为 `MSIZ / (10·TDIV)`（不超过 20 GSa/s），不按真机的采样率表取整。这样主机给出的任意点数（30 000、100 000、12 345…）都能完整回读（AC-2）。
- 触发点在记录中央；触发在触发源通道上按电平与沿搜索（滞回 0.16 格）。
- `AUTO`、`NORM`：回读从 `FP 0` 开始时采集一次，之后的分块（`FP > 0`）读同一条记录，分块拼接得到一条完整、连续的记录。`NORM` 没有触发时保留上一条记录。
- `SINGLE`：立即尝试采集；有触发则记录并变为 `STOP`（`TRMD?` 回复 `TRMD STOP`）。暂时没有触发（例如校准仪输出尚未打开）时保持等待，之后的 `*OPC?`、`WAIT`、`TRMD?` 与回读都会再次尝试。
- `STOP`：不再采集。还没有任何记录时，回读取通道当前的信号并在事件历史中标为 `forced`，而不是返回空波形让主机的回读循环停不下来。
- 噪声：输入端 200 µV rms 白噪声，12 位 ADC，范围 ±5 格（超出屏幕的部分被削顶）。这两个数值是假设值，不来自数据手册，可在测试台中用 `hidden` 覆盖（如 `scope: {hidden: {acq: {noise_rms: 0}}}`）。噪声是种子、通道与采集序号的纯函数，不随脚本停顿的真实时间变化。

## 6. 私有文本波形回读（D3，FR-4）

回复是一行：`C1:INSP " 1.000000e-03 -4.995117e-01 … "`。按空白切分后前 2 个 token（`C1:INSP` 与 `"`）与最后 1 个 token（`"`）是非数值文本，其余都可以被 `float()` 解析：

```python
data += map(float, reply.strip().split()[2:-1])
```

- 回复中只有末尾一个换行，不含 `;`，不含 `TCPIP`；
- `FP` 是本次第一个样本的下标，`NP` 是点数（`0` 表示到记录末尾，但每次最多 30 000 点），`SP` 是抽点间隔（`0`、`1` 为每点都取）；`SN`（段）只保存，分段采集不建模；
- `FP` 超出记录时回复 `C1:INSP " "`（没有数值）。

## 7. 示波器校准仪

| 命令 | 实现 |
| --- | --- |
| `:CHANnel<n>:BASE:WAVe SQUare\|PULSe\|SINe\|DC\|TRIangle\|RAMP` | 波形；方波与脉冲在 `偏置 ± 幅度/2` 之间切换 |
| `:CHANnel<n>:BASE:FREQuency`、`:BASE:AMPLitude`、`:BASE:OFFSet` | 频率、幅度（按 `:AMPLitude:UNIT` 的 VPP / VPK / VRMS）、偏置 |
| `:CHANnel<n>:BASE:DUTY <比例>` | 占空比，`0.25` 即 25 %；大于 1 的值按百分数读 |
| `:CHANnel<n>:LOAD <Ω>` | 幅度按负载设定定义（D13）：`50` 为 50 Ω，`10000` 及以上为高阻（开路电压） |
| `:CHANnel<n>:OUTPut 0\|1` | 输出开关 |
| `:SYSTem:CLKSource INTernal\|EXTernal` | 保存，可查询 |

输出阻抗 50 Ω；方波与脉冲的谐波到 1 GHz（谱线数受引擎上限约束，截断时信号带保真度说明，D33）。

## 8. 物理模型与频响（FR-5）

信号由电路求解得到，不按命令伪造（D13）：校准仪的 50 Ω 源经线缆接到示波器的输入阻抗，再经前端（2.5 GHz 一阶带宽）、噪声与 ADC 形成记录。线缆（`coax_cable` 新参数 `r_skin_1mhz`）带直流电阻 0.05 Ω 与趋肤效应（1 MHz 时 0.15 Ω，随 √f 增大，电阻与内电感同时增大）：频率越高，方波边沿后的建立越慢、平顶越低。示例脚本在 1 kHz–1 MHz 上测得的相对幅度约为 0.9994 → 0.9977，随频率单调不增，差异落在 12 位 ADC 的量化台阶上（AC-3）。

需求第 9 节待澄清项的处理：

1. **D3 的实现方式**：新增平台组件类型（`lecroy_*`），与 Siglent SDG/SDS 的私有命令同样做法；校准仪最初也有专用组件，issue 42 之后改为通用信号源加参数映射，命令全部声明；平台核心只增加了与型号无关的三处：定义的 `unknown_commands`、定义可以给公共命令（如 `*CLS?`）自己的行为、测试台实例的 `idn`。
2. **频响模型精度**：理想信号源 + 电路求解（输出阻抗、负载设定、输入阻抗、线缆损耗与趋肤效应、前端带宽）+ 噪声与量化；不是按某台真机的实测传递函数建模。
3. **`*OPC?` 与虚拟时间**：预设使用步进时钟（`run.clock: stepped`），同一种子、同一命令序列结果逐位一致（AC-4）；`*OPC?` 等待未完成的单次采集。需要采集消耗虚拟时间时改为 `clock: command`（每次采集推进约 10 格时间加 1 ms），噪声仍不随停顿变化。

## 9. 调试与回放（FR-7、FR-8）

- 随机种子：测试台 `run.seed`；
- 运行记录与回放：`simubench serve … --record-dir runs`，`simubench replay <记录> --definitions examples/lowfreq-cal/instruments`（D36；两台仪器的定义在工作区中，回放时指明其目录）；
- 推进虚拟时间、注入故障（超时、断连、错误码、参数漂移）：Web Bench、MCP 或测试台 `faults`（D27）。

## 10. 验收

| 验收标准 | 测试 |
| --- | --- |
| AC-1 主机流程在 raw socket 上全部完成，无错误 | `acceptance/test_lowfreq_cal_scenario.py::test_ac1_*` |
| AC-2 30 000 与 100 000 点分块回读，拼接即记录 | `test_ac2_*`；`tests/test_lowfreq_cal.py::test_pieces_join_into_the_whole_record`（含 12 345 点） |
| AC-3 频响点数、量级、单调性 | `test_ac3_*`、`test_frequency_response_is_physical_and_monotonic` |
| AC-4 同一种子结果一致 | `test_ac4_*`、`test_same_seed_same_numbers_other_seed_other_noise` |
| FR-1–FR-5 逐条命令 | `tests/test_lowfreq_cal.py` |
