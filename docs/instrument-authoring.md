# 由用户与 Agent 搭建仪表（设计稿）

状态：第 1–6 节已实现（总 issue #39）；第 7 节为第二期的设计稿（总 issue #123）；第 8 节为组件粒度的设计稿，从示波器开始。对应 D3 第 3 层“搭建仪表”，以及 architecture.md 3.3–3.5、7.5 中已规划但尚未实现的部分。

## 0. 目标与衡量

SimuBench 不可能为用户提供所有型号的仪表。平台要提供的是**搭建仪表的能力**：工程师或 Agent（经 MCP）根据厂商手册、自己的设计规格、或在研仪表的接口契约，自己写出仪表定义，不需要修改 SimuBench 的源码。依据由作者提供和管理；带 `reference` 的定义与内置真实型号适用同一套检查（D42）。

**衡量标准**：不改平台代码，只用 YAML 定义（第二遍：只通过 MCP 工具）重建低频校准工作台（[lowfreq-cal.md](lowfreq-cal.md)），并通过同一组验收测试（`tests/test_lowfreq_cal.py`、`acceptance/test_lowfreq_cal_scenario.py`）。

**结果（#48）**：已达成。低频校准工作台的两份定义只用平台组件与声明式字段写成，`library/components/lecroy.py` 已删除（`calibrator.py` 在 #42 删除），两份定义背后不再有专用代码；从空工作区只调用 MCP 工具（`list_components`、`create_definition`、`validate_definition`、`create_bench` / `edit_bench`、`run_cases`）搭出的工作台与示例的测试台指纹相同，并通过同一组测试（`tests/test_lfcal_mcp_rebuild.py` 在这个工作区上再跑一遍 `tests/test_lowfreq_cal.py` 与验收场景）。没有需要插件的部分。为此补充的通用能力见 2.5。

原则：

1. **声明式优先**：常见的命令方言、应答格式、波形传输与运行控制都能在定义文件里写出来；
2. **代码是出口，不是常规路径**：确实无法声明的行为用插件组件实现，接口与内置组件相同（architecture.md 3.4）；
3. **Agent 只写定义，不写代码**：MCP 产出的是可读、可 diff 的 YAML，由工程师审阅（D9）；
4. **先校验再运行**：定义文件与测试台共用一套诊断（错误码、JSON 指针、修改建议，architecture.md 3.5），Agent 据此自我纠错。

## 1. 现状与缺口

低频校准工作台的两份定义背后，平台包里新写了约 630 行 Python（`library/components/lecroy.py`、`calibrator.py`）。按原因归类：

| # | 缺口 | 证据 |
| --- | --- | --- |
| G1 | 应答格式不能声明：回显命令头与单位（`C1:VDIV 200E-3 V`）及其开关（`COMM_HEADER`）、工程计数法、键值参数（`TRSE EDGE,SR,C1`）、带倍率后缀的点数（`MSIZ 10K`）、枚举别名与数值换算（校准仪 `SINe` → `SINusoid`、占空比比例 → 百分数） | `lecroy.py` 的 `setting`、`trig_select`、`waveform_setup`、`memory_size`；`calibrator.py`；此前的 `siglent_sdg.py` 同理 |
| G2 | 波形传输格式写死在各型号组件中：二进制块、Siglent WAVEDESC、LeCroy 文本回读各一份 | `scope.py` `data`、`siglent_sds.py` `wav_data`、`lecroy.py` `inspect` |
| G3 | 采集与运行控制按型号重写：记录长度规则、AUTO/NORM/SINGLE/STOP、何时重新采集、噪声与 ADC | `siglent_sds_acquisition` 与 `lecroy_acquisition` 大量重复 |
| G4 | 没有用户代码的扩展点：组件只能在 `simubench` 包内注册 | `engine/component.py` 只注册 `library.components` |
| G5 | MCP 能读定义（`list_catalog`、`get_definition`），不能创建、编辑、校验定义；目录不列内部组件类型 | `mcp_server.py`；`benchfiles.catalog` 过滤 `category == "internal"` |
| G6 | 定义没有静态校验：组件类型、op 名、状态绑定写错，启动会话时才以异常报出 | `simubench validate` 只接受测试台 |
| G7 | 测试台校验依赖内置组件名：驱动源认 `output_stage`，串联配件认 `coax_cable`，输入阻抗曾只认 `input_port` | `engine/validate.py` |
| G8 | 用户没法给自建仪表写验收：`cases.yaml` 的运行器只在仓库的 pytest 中 | `tests/test_real_models.py`、`library/coverage.py` |

## 2. 声明式命令与应答（G1）

已实现（#42）：低频校准工作台两份定义中的设置命令、`TRSE`、`MSIZ`、`WAVEFORM_SETUP` 与校准仪的全部命令都改为声明，`calibrator.py` 已删除，`lecroy.py` 只剩采集、运行控制、文本回读与 `PACU`（槽号选择状态，留给插件或后续扩展）。与设计稿的差别：数值格式放在已有的 `format` 中（`number: engineering`、`integer: engineering`），不在 `dialect` 中重复；多状态命令的查询不写 `reply` 时按参数顺序回答（`EDGE,SR,C1,HT,OFF`）；新增状态的 `ratio`（比例量也接受百分数，40 → 0.4）；信号源的幅度单位（VPP / VPK / VRMS）是 `function_generator_core` 的参数 `amplitude_unit`，因为它依赖两个设置（单位与波形），不是查表与直线能表达的。

### 2.1 方言

定义新增 `dialect`，说明这台仪表的应答怎么写，默认值即现在的 SCPI 行为；数值写法用已有的 `format`：

```yaml
dialect:
  echo_header: $comm.header      # off | short | long，或绑定到一个状态量（COMM_HEADER 可切换）
  # long_form: $system.longform   # 可选：布尔开关，开时命令头与枚举应答用长格式（Infiniium，#602）
  multipliers: {K: 1.0e3, M: 1.0e6, MA: 1.0e6, G: 1.0e9}   # 无单位数值的倍率后缀（点数）
format:
  number: engineering            # 现有 nr1/nr2/nr3 之外新增 engineering：200E-3、10E+3
  boolean: word
  enum: short
```

**数值也可以按手册写成 SI 前缀加单位（#289）：`format: {number: si, significant: 9, unit: Hz}` 把 4 MHz 回成 `4.00000000MHz`、400 MHz 回成 `400.000000MHz`（有效数字合计 `significant` 位，前缀取使尾数落在 1–1000 的那一档，舍入进位到 1000 时换下一档；`unit` 不写时用设置的单位）。百分比参数把设置的 `unit` 写成 `PCT`：`80` 与 `80%` 都写入 80，回读按原格式。

**枚举列表** `type: enum_list`（#252）：一个设置保存几项选项，如读数元素 `:FORM:ELEM:SENS VOLT,CURR`。写入接受逗号分隔的多个词（长短形式均可），按 `options` 的顺序保存与回读，与写入顺序无关；不认识的词报 -224，原值不变。操作命令的 `args` 可以写 `$<设置路径>`，执行时取该设置的当前值：B2901A 的 `MEASure?`、`FETCh:ARRay?` 等写 `args: {elements: $format.elements.sense}`，回的元素随 `:FORM:ELEM:SENS` 变化。也可以写 `{from: $<设置路径>, map: {...}}`，取值后经过与参数绑定相同的映射：SR830 的 `OUTR?` 写 `args: {target: {from: $display.quantity1, map: {0: demod.X, 1: demod.R, ...}}}`，回的量随 `DDEF` 变化（#364）。

枚举的查询回复默认是选项的短格式（`enum: short`）或定义里的写法（`as_defined`）。仪表的回复词与它接受的设置词不同时，在状态上写 `replies`，按选项给出回复词，未列出的选项照常回复。例如 DL3021 的 `:FUNC CURRent` 查询回 `CC`（#268）：

```yaml
function.mode:
  type: enum
  options: [CURRent, RESistance, VOLTage, POWer]
  replies: {CURRent: CC, RESistance: CR, VOLTage: CV, POWer: CP}
```

### 2.2 应答模板

命令可给 `reply` 模板；`{header}` 按 `echo_header` 展开（含通道前缀 `C1:`，`off` 时为空并去掉其后的空格），`{value}` 是绑定状态量按方言格式化的值，`{<状态路径>}` 引用其他状态量：

```yaml
- header: "C<ch>:Volt_DIV"
  state: ch.scale
  reply: "{header} {value} V"
- header: "TRig_SElect"
  reply: "{header} {trigger.type},SR,{trigger.source},HT,{trigger.hold}"
  params:
    - {name: type, type: enum, options: [EDGE], state: trigger.type}
    - {name: rest, type: pairs, keys: {SR: trigger.source, HT: trigger.hold, HV: null}}
```

`type: pairs` 是新的参数类型：`名,值,名,值…`，每个名映射到一个状态量（`null` 表示接受并忽略）；多个状态量按一次设置整体校验，任一不合法则全部不改（与 `siglent_sdg` 的 `_set` 相同的语义）。有 `params` 中的 `state` 绑定、没有 `op` 的命令是新的第三种命令形式（多状态命令），现在的“`state` 与 `op` 二选一”放宽为“`state`、`op` 或参数绑定之一”。

### 2.3 组件参数的映射

组件参数绑定除 `$路径` 外，允许映射与换算，用来把方言的取值翻成通用组件的取值：

```yaml
params:
  function: {from: $ch.wave, map: {SQUare: SQUare, PULSe: SQUare, SINe: SINusoid, DC: DC}}
  duty: {from: $ch.duty, scale: 100}           # 比例 → 通用组件的百分数
  z_in: {from: $ch.coupling, map: {D50: 50.0}, default: 1.0e6}
  resolution: {from: $sense.nplc, table: {0.02: 3.0e-4, 1: 3.0e-6}}   # 数值查表：取不大于该值的最大表项
```

只支持 `map`、`table`、`scale`、`offset`、`default`，不引入表达式语言：表达式难以校验、难以让 Agent 写对，复杂换算走插件。`table`（#87）按数值查表：取不大于设置值的最大表项（NPLC 0.5 用 0.2 的值），没有更小的项时取最小项，之后同样过 `scale` / `offset`。

**几个设置共同决定一个参数**（D70，#1020）：`map` 或 `table` 的取值、以及 `default`，可以是另一个映射（或 `$路径`），选中时按它自己的设置再取一次，可以嵌套多层。例（N9042B 的前放增益：开关 → 频段 → 中心频率）：

```yaml
preamp_gain:
  from: $power.gain
  map:
    "False": 0.0
    "True": {from: $power.gain_band, map: {FULL: 30.0, LOW: {from: $freq.center, table: {0: 30.0, 3.6e9: 0.0}}}}
```

嵌套里引用的每个设置都会通知组件；静态校验在嵌套映射所在的位置检查它的写法（`E-SCHEMA`）、引用的设置（`E-DEF-STATE-UNKNOWN`，指针指到那一层的 `from`）和 `map` 的键（`W-DEF-MAP-KEY`）。同一组数值要在几处用时，用 YAML 的锚点与别名（`&nf_1a` / `*nf_1a`）写一次。

### 2.4 公共命令与未知命令

已实现（低频校准工作台）：定义可以给公共命令自己的行为（`*CLS?`），`unknown_commands: ignore`（D42）。保持不变。

### 2.5 #48 补充的声明式字段与通用组件

把参考示波器最后的专用行为改为声明时补充（都与型号无关）：

- **按参数区分的命令** `when`：几条命令共用一个命令头，按参数的取值选择（`C1:INSPECT? "DATA_ARRAY_1"` 回读数据，`"WAVEDESC"` 给出描述）；都不匹配时回复 -224。写在这些命令之后、不带 `when` 的同头命令是兜底：其他取值与省略参数时用它（Infiniium 的 `:SYSTem:ERRor? [STRing]`：STRing 回码与文字，不带参数只回码，#602）。静态校验只在 `when` 能区分、或后一条是兜底时才不报 `E-DEF-HEADER-DUPLICATE`；兜底写在 `when` 命令之前则那些命令永远不执行，照样报错。
- **应答头的两个开关**（#602）：`dialect.echo_header` 可以绑定一个布尔设置（开为短命令头，Infiniium 的 `:SYSTem:HEADer`）；`dialect.long_form` 绑定另一个布尔设置，开时命令头与枚举应答都用长格式（`:SYSTem:LONGform`：`:TIMEBASE:REFERENCE CENTER`）。公共命令的查询始终不带头。
- **按大小排序后写入** `order`（#1011）：有的命令接受两个值、顺序任意，由仪表把较小值写到下限、较大值写到上限（DSO-X 2000 的 `:TRIGger:GLITch:RANGe`：“较小的值成为 greater_than，较大的成为 less_than”）。命令级 `order: {ascending: [greater_than, less_than]}` 按从小到大列出参数名：解析后把这些参数的值排序，依次写入它们各自的设置；各设置的范围在排序之后校验（`RANGe 12E-9,40E-6` 中 12 ns 低于“小于”的下限，排序后写到“大于”合法）。查询仍按 `params` 的顺序回读。只用于写设置的命令；列出的参数须是必填的 `float` / `int`、各写一个 `state`，不带 `keywords` / `sets`（模型校验）。
- **参数的连带设置** `sets`：一个写设置的参数可以同时写一组常量，例如 CONFigure 的量程参数 `sets: {sense.range_auto: false}`（量程一给就关闭自动量程）。连带设置只跟随明确的取值（数值、MIN、MAX）：参数写的是该设置的特殊字（`special`，如 AUTO）或 DEF 时不写，保留命令 `writes` 的选择（#325：`CONF:VOLT:DC AUTO` 仍为自动量程）。常量与参数一起校验、一起生效；静态校验用 `E-DEF-WRITE` 检查常量。
- **关键字代写** `keywords`：参数写某个关键字（MIN、MAX、DEF 或特殊字；MINimum 等长形式同义）时，改写表中给出的设置，参数自己的设置只在表里写出时才写。用于关键字的含义不是"参数声明的上下限"的场合，例如 34465A 的 CONFigure 分辨率 `keywords: {MIN: {sense.resolution: 0, sense.nplc: 100}}`（最细分辨率选最长积分时间，#344）。表中常量同样由 `E-DEF-WRITE` 检查。
- **整数序号映射为词**（#333）：词参数（如 `ext_slope`、`lockin_input_mode.mode`）可以绑定一个整数设置，只要它的取值范围小（不超过 64 个值）、`map` 给出每个值对应的词（或嵌套映射）；静态校验逐值检查。SR86x 的枚举是序号加词的别名（`RTRG POS` 与 `RTRG 1` 同义、查询回 1），用整数设置加 `special` 写词，再映射到组件的词。
- **响应格式 `general`**（#383）：`format.number: general` 即 C 的 `%g`，`digits` 为有效位数（SR86x 的 `0.951359`、`2.3346669042e-09`）；组件读数同样要 `readings: format`。角度参数（单位 `DEG`）也接受弧度后缀 `RAD`/`MRAD`/`URAD`，换算为度保存。操作的 `args` 中 `$设置` 可以写在列表里（逐项取值，SNAPD? 的四个数据通道指派）；一个参数选读哪个设置（SR86x 的 `OUTR? j`，#401）写成每个取值一条带 `when: {j: DAT2}` 的命令，各自 `names: [$display.dat1]`，与 `CDSP j,p`（`index_words` + `index_numbers` 的索引参数写 `display.dat{ch}`）配合；组件参数映射的取值也可以是 `$设置`（`{from: $output.offset_on1, map: {"0": 0.0, "1": $output.offset1}}`：COFA 开时取 COFP 的百分数）。
- **组件应答的数值格式** `format.readings`（#366，D54）：默认 `component`，标记值、迹线点、计数器读数等由组件按自己的位数写；手册的回复统一一种格式时写 `readings: format`，这些数值也按定义的 `format` 写（N9320B 16 位、53220A 15 位有效数字）。位数本身是定义参数的组件（万用表 `dmm_adc.digits`、`trigger_model.digits`、`waveform_transfer`）不受影响。组件代码用 `self.reply_number(value, digits)` 写数值应答。
- **未建模的词** `not_modeled`（设置或参数，#360，D53）：真机接受、模型没有实现的词，写成 `{INFinity: 连续扫描未建模}`（SCPI 大小写，长短形式都匹配）。写这个词报 -224，错误事件标为未建模并带原因，不会被当作数值按范围拒绝。只用于拒绝；接受一个词用 `special` 或 `keywords`。数值参数的 INF/INFinity、NINF/NINFinity 本身由引擎解析为 ±∞。
- **索引参数** `index: true`：多状态命令的一个整数参数选择其他参数写哪组设置，状态路径中写作 `{名称}`：`PACU 1,AMPL,C1` 写 `measure.p1.kind` 与 `measure.p1.source`；查询也带它（`PACU? 1` → `PACU 1,AMPL,C1`），没有设置的可选值不写出（`PACU 1,NULL`）。静态校验按索引的取值范围检查每个状态。枚举的默认值可以是 `""`（尚未设置）。
- **索引也可写号**（#333）：`index_numbers: true` 时索引参数既可写词也可写号（SR86x 的 `CDSP DAT1` 与 `CDSP 0` 同义），号不在 `index_words` 的取值中时报 -222；不写时只认词（DSO-X 的 `:VIEW CHANnel2`）。整数设置经词写入后存为 `1.0`，参数映射与读数参数表都按整数匹配。
- **按词选择的索引**（#248）：索引也可以是一个词，`index_words` 给出每个词对应的号，例如 DSO-X 的 `:VIEW CHANnel2` 写 `ch2.display`（`{name: source, type: enum, index: true, index_words: {CHANnel1: 1, …}}`，命令只有索引参数时用 `writes: {"ch{source}.display": true}` 给值）。设置时索引按它自己的位置取（`:TRIG:LEV 0.5,CHAN2` 的第二个参数），查询时只带索引参数；可省略的索引用 `index_default` 给号或 `$设置`（`:TRIG:LEV?` 不带信源时取当前触发源）。`echo: false` 让查询回复不重复索引（SR830 的 `DDEF? 1` 只回 `j,k`，#273）。
- **操作应答的字段**：操作可以在结果中给出命名字段（`@op(fields=...)`），命令的 `reply` 模板引用它们：`'{header} {kind},{value} {unit},{status}'`（`C1:PAVA AMPL,200.1E-3 V,OK`）。组件目录与静态校验都认得这些字段。
- **通用组件与操作**：`system`（`clear_status`：`*CLS` 及其查询的回复；`reset`：与 `*RST` 相同的预置）；`scope_acquisition.measurement`（按 `measurements` 表的命名测量，值不能测量时为 `invalid_value` 与 `status` 的第二项）；`waveform_transfer.describe`（按 `descriptor` 的格式写出记录的点数、首点、抽点、采样间隔、偏移、增益、时基与采集次数）。输入阻抗、前端耦合用 `input_port` 与 `scope_frontend` 加参数映射表达。
- **一族设置** `each`（UXR1104B 书签、波形区、显示颜色）：状态名里写 `{n}`，`each: {n: {first: 1, last: 100}}` 展开为每个号一条设置（`label` 里的 `{n}` 同样替换；至多 1000 条）；同名写出的一条覆盖族里的那一条（区 1 只读）。命令用同名的索引参数或**命令头后缀**选择：`<ch>` 以外的命名后缀（`:DISPlay:BOOKmark<n>:XPOSition`，`suffixes` 列出取值）与索引参数一样替换状态路径、`writes` 与操作 `args` 里的 `{n}`；静态校验按 `suffixes` 的取值逐个检查。
- **不随预置变的设置** `keep_on_reset: true`：`*RST` 与型号的预置命令不改它，上电仍取 `default`（Infiniium 的 Default Setup 不改用户偏好、显示颜色与探头延迟）。
- **身份里的设置** `identity.idn` 可写 `{$<设置路径>}`，`*IDN?` 取该设置的文字（`:SYSTem:PERSona` 改厂商与型号）。
- **只有常量的命令** `system.none`：不做任何事的操作；命令的 `writes` 就是它的全部效果（`:DISPlay:BOOKmark:DELete:ALL`），查询回 `reply` 模板的常量（`:SYSTem:CAPability:CHANnel? COUNt` → `"4"`，按参数取值用 `when` 分开）。`when` 的取值与特殊字（`special`）都按 SCPI 词匹配长短形式（`COUN` 即 `COUNt`）；取值为 ±∞ 的特殊字是模式词，不当作上下限（余辉的 `MINimum`、`INFinite`）。
- **测试台编辑** `set_identity`：设置实例的 `idn`、`serial`、`firmware`（MCP 原来无法写出 `*IDN?` 的型号串）。

### 2.6 通道列表参数（#183）

电源与 DAQ 的命令带通道列表参数：`VOLT 5,(@1:3)`、`MEAS:VOLT? (@1:3)`、`ROUT:SCAN (@101:110)`。声明与语义：

- **参数 `type: channel_list`**：声明通道列表，展开（expand）语义——命令对列表中每个通道各执行一次；查询按列表顺序逗号拼接回复。命令的其他参数经 `state` 绑定设置，通道前缀走既有的 `ch.` 机制（`channel_state` 的键，如 `state: "ch.volt"`）；每通道自己的量程（`channels.overrides`）对同一次写入分别生效。`optional: true` 与 `default: [1]` 表示列表可省略（B2901A 的 `(@1)`），省略时用 `default`；`default` 也可以写成一个 `int_list` 设置（`default: $scan.list`：DAQ970A 省略列表即作用于当前扫描表，表为空时 -221，#423）。查询带 MIN/MAX/DEF 而省略列表时只回一个值（`NPLC? MAX`）。每条命令至多一个通道列表参数；它本身不写设置。
  ```yaml
  - header: "VOLTage"
    params:
      - {name: level, type: float, unit: V, state: "ch.volt"}
      - {name: channels, type: channel_list, optional: true, default: [1]}
  ```
- **操作命令**也可以带通道列表参数（`MEAS:VOLT? (@1:3)`）：操作逐通道执行，回复在各自通道的上下文里过 `reply` 模板后按列表顺序拼接。
- **作为值保存**（`ROUT:SCAN (@101:110)` 把列表存为状态、查询按手册格式回读）不需要专门的参数类型：状态的 `type: int_list` 接收 `(@…)` 与回读 `(@101:103,110)`（连续通道折叠为 `a:b`；状态 `min`/`max` 对列表中每个通道号生效）。
- **解析**：`(@` 开头、`)` 结尾；逗号分隔、`a:b` 范围展开（含于参数括号分组内，`split_params` 把括号组当一个程序数据）；列表顺序即展开与回复顺序。空列表（-108）、重复通道（-108）、倒序范围（-222）、格式错（-102）、通道不在号域内（-114，同 `<n>` 后缀）都报错。倒序范围与重复通道由定义声明（#260）：`channels.lists: {descending: true}` 接受 `(@2:1)` 并按书写顺序展开（2、1，B2900 手册的写法），`repeated: true` 接受重复通道、每次出现各答一次；`empty: true` 接受空表 `(@)`（DAQ970A 的 `ROUT:SCAN (@)` 清空扫描表，#418），只作用于整表写进 `int_list` 设置的命令（`state` 为列表设置的命令，如 `ROUT:SCAN`）；按通道展开的写、查询与操作（`VOLT 5,(@)`、`VOLT? (@)`、`MEAS:VOLT? (@)`）即使声明了 `empty` 仍报 -108，不改任何通道，查询也不会静默无应答（手册只在 `ROUT:SCAN` 上写明空表合法，#420）；默认三者都报错。
- **通道号域**（#207，D51）：通道号不是 `1..count` 时，用 `channels.numbering` 按通道顺序声明号码块；第 k 块从内部序号 `sum(前块 count)+1` 起，覆盖 `first .. first+count-1`。SCPI 里出现的通道号——`(@…)` 列表及其 `default`（`default: [101]` 写仪表的号）、命令头的 `<n>` 后缀、`int_list` 状态（`ROUT:SCAN`）——都按声明的号码读写；状态路径 `ch<n>.*`、组件名、`channels.overrides`、连线与端口的 `{n}` 仍用 1..count 的序号。范围的中间号可以不声明（跨槽位的 `(@101:205)` 只保留已声明的通道），但显式写出的号与范围首尾必须是已声明的通道，否则 -114。
  ```yaml
  channels:
    count: 4
    numbering:            # 仪表上的 101、102、201、202；内部序号仍是 1..4
      - {first: 101, count: 2}
      - {first: 201, count: 2}
  ```
- **随状态变化的上下限**（#437）：组件可实现 `state_limits(path)`，按当前状态给出更窄的 (min, max)；`MIN`/`MAX` 查询与关键字都用它（33622A 的 `FREQ? MAX` 随波形变化，由 `waveform_synth.max_frequency` 提供）。超限的处理仍由组件的 `on_state` 决定（拒绝或置为上限）。
- **整表写入与常量**（#300）：通道列表参数可以带 `state`（一个 `int_list` 设置）：列表按仪表的号整表写进它，写设置的命令照常展开到各通道，操作命令只执行一次（34970A 的 `MEAS:VOLT:DC? (@101:110)` 写扫描表后跑一次扫描）。带通道列表的命令同样先写 `writes` 常量、再写参数的 `sets`（`CONF:VOLT:AC 10,(@101)` 选交流并关闭自动量程）；可省略的参数遇到 `(@…)` 视为省略，列表留给后面的通道列表参数。操作命令的参数也可以带 `state`：先写设置（连同 `sets`），再执行操作（`MEAS:VOLT:DC? 10,(@101)` 用 10 V 量程）。
- **选中的输出**（#295）：`channels.selected` 指向一个 int 设置（选中输出的通道号），省略通道列表时命令作用于它，而不是参数的 `default`。`INSTrument[:SELect]` 与 `NSELect` 写同一个设置；名称写法用设置的 `special`（`{CH1: 1, P25V: 2}`），查询用 `reply: "CH{value}"`。E36312A 采用：`INST:SEL P25V; VOLT 5` 改输出 2。
- **与既有写法并存**：同一型号可同时有 `<n>` 数字后缀（`OUTP1`，命令头后缀，既有机制）、通道列表参数（本节）与 `INST:SEL`/`INST:NSEL`。前两者互不冲突；选中输出见上一条。

### 2.7 无参数命令（#119）

- **同一命令头：set 调操作，query 读设置**（`OPER` / `OPER?`）。操作命令加 `read: <设置>`：query 形式不调用操作，按 `reply` 模板回读该设置（默认 `{value}`），set 形式仍调操作。`{value}` 按定义/命令的 `format` 格式化，所以布尔量在 Fluke 的 numeric 格式下回 `1` / `0`。
  ```yaml
  - header: OPER
    op: out.operate
    args: {value: true}
    read: out.operate
  ```
- **无参数、只置态的命令**（`LOCAL` / `REMOTE` / `LOCKOUT`）：状态命令加 `set_value`，没有参数时把该常量写入绑定设置，`query: false` 时没有查询；带参数的 set 仍按参数解析，两者不能同时用。
  ```yaml
  - header: LOCAL
    state: interface
    set_value: LOCAL
    query: false
  ```
- 静态校验：`read` 的路径必须存在（`E-DEF-STATE`）；`read` 只能与 `op` 同用，`set_value` 只能与无参数的状态命令同用（模型校验）。运行时写入的常量仍按状态的类型、范围与枚举校验。

### 2.8 命令头写常量（#117）

`CONFigure` 一类命令用命令头选择功能，同时带量程/分辨率参数。多状态命令可以加 `writes: {<设置>: <常量>}`：参数全部校验通过后，先写常量（参数因此在新功能下生效），再按参数顺序写设置；常量不合法时整条命令不生效，错误码同参数。

```yaml
- header: ":CONFigure[:VOLTage]:DC"
  query: false
  writes: {sense.function: VOLT}
  params:
    - {name: range, type: float, unit: V, min: 0.1, max: 1000.0, optional: true, state: sense.range}
    - {name: resolution, type: float, unit: V, min: 0.0, max: 1000.0, optional: true, state: sense.resolution}
```

查询仍只回答参数（常量不是参数）。`writes` 用在写设置的命令上，或用在操作命令上：操作执行前先写常量（#308：`:MEASure:CURRent?` 同时把测量功能切到电流，`writes: {sense.function: CURRent}`）；状态命令不能带 `writes`（模型校验）。静态校验的 `E-DEF-WRITE` 检查常量是枚举的选项、布尔量、范围内或（整数）范围的值，引用的设置必须存在。枚举常量按 SCPI 词规则判定（#625）：长短格式、复合词逐节点都接受（`VOLT`、`volt:ac`），引擎存入的是它命名的选项本身；面板键的取值仍须写选项的原样，否则按键与显示值的比较不亮。设置的特殊值（`special`，如过载的 9.9E+37）不受范围两侧限制——与运行时一致。

### 2.9 类别默认与兄弟继承（extends，#596）

内置库为每个类别（`class`）保存一个默认块（`src/simubench/library/class_defaults.py`），目前是各类别的通用 errors 表：该类别全部内置型号逐字相同的那部分错误文本（器件特有的码与措辞不同的码留在型号文件里）。loader 在 `model_validate` 之前把定义所属类别的默认块深合并到定义数据之下：定义数据逐键胜出，`errors` 按 code 逐条胜出——文件里只写与类别表不同措辞的码和器件特有的码，其余的码由类别表给出。默认表服务于真实模型（D42，有 `reference`）——表条目会整体替换引擎的「标准文本；细节」回包，所以不带 `reference` 的定义（`sb-*` 验证仪器、玩具定义）维持引擎标准文本不变。同一类别的真实模型无论在库内还是自己的目录都拿到同一张表；不想用某条措辞，在自己的文件里列出该码即可。给类别添型号时，类别表只收新型号手册逐字一致的条目，其余写进型号文件。

定义还可以声明 `extends: <内置型号 id>`，从同库的另一台仪表出发：loader 先解析父定义（父定义自己的 `extends` 也照此解析，单亲、禁止环，成环或父定义不存在都在加载时报错），再把本定义的数据深合并上去。合并次序是 **类别默认 → 父 → 子**；dict 逐键递归合并（`errors` 按 code），**列表整体替换**——要改动命令或组件时重声明整张列表，只想加一条状态时只写那一条。父定义按「子定义所在目录 → `SIMUBENCH_DEFINITIONS` 目录 → 内置库」的顺序查找；类别默认在最底层合并一次，按子定义的 `class` 取表，所以从内置型号出发的自定义义同样拿到它（父定义文件里的通用错误行已上收进类别表，不靠继承）。

**子定义里写 `键: null` 丢弃继承来的该键**：dict 键（一条状态、一个端口组、`specs` 的一项）、`errors` 的单个 code、整个列表（如 `components: null`）都适用；null 在全部层合并完才生效，下层与类别默认都带不回来，父定义没有该键时等于没写。内置库的 keysight-53230a（`extends: keysight-53220a`）、keysight-daq970a（`extends: keysight-34970a`）与 keysight-b2912b（`extends: keysight-b2901a`，丢弃单通道的仪器级状态、端口与组件）按此改写；coverage 表与用例文件仍按型号各写，不随 extends 合并。

### 2.10 频谱仪前端的非理想参数（#954）

频谱仪前端的非理想全部是功能块的声明参数，**不声明时回复与理想接收机逐字节相同**（`sb-sa-1` 不声明）。参数取自数据手册，`specs` 里逐项注明手册的表与页；手册没有的项不声明、不估计。块的划分与公式见 `signal-chain.md` 5.2，能力清单见 `component-map.md` 的频谱仪节：

- `specan_input`：`flatness`（`[[频率 Hz, dB], …]`，分段线性、表外为 0 dB，作用于显示电平）、`preamp_gain`（已有）、`preamp_noise_figure`/`iip2_dbm`/`iip3_dbm`/`p1db_dbm`（前置放大器自己的指标，块内按衰减折算到输入参考）。声明 `preamp_noise_figure` 即前放在链中：`preamp_gain: 0` 只把增益设 0，噪声系数仍在链上；无前放就不声明这些键。前放是选件时（N9320B 的 P03），前放的参数写在定义的 `options` 里而不是 `components` 里，测试台实例用 `options: [P03]` 声明装了它；前放开关命令写 `option: P03`，后面跟一条同命令头、只读的命令给出未装选件时的回复（#1004，字段见 `real-models.md`）。实例装了哪些选件可在 Web 检查器的“选件”一节或 MCP 的 `set_options` 编辑操作中修改，型号概览页列出每个选件改变的参数与命令；手册写明 `*OPT?` 的回复格式时，定义写 `identity.options_reply` 让内置 `*OPT?` 列出已装选件（#1007）。`flatness` 的个体偏差按频段写（`deviations` 的 `bands`，`metrology-authoring.md` 7.1）：数据手册的平坦度是各频段的 ± 限值，标称为 0，参考频率处为 0。

  **按通路给指标的前放**（N9042B 的 PA，#1020）：数据手册按“通路”（前放关、开……）分别给出 DANL、SHI、TOI、压缩与频响，都是整条通路的数值。前放起作用时，这些数值全部写在输入块的前放参数上（`preamp_noise_figure`、`iip2_dbm`、`iip3_dbm`、`p1db_dbm`），混频器在这条通路上写 `null`（`specan_mixer.noise_figure` 可为 `null`：不计入）；前放不起作用时反过来。这样写用 2.3 的嵌套映射，按开关、频段与中心频率取值。手册写明自动衰减计入前放增益时（X 系列：`Atten = RefLevel + PreAmpGain − MaxMixerLevel`），输入块写 `preamp_in_mixer_level: true`，过载判断同样计入。每条通路的频响各写一条带 `when` 的个体偏差（`metrology-authoring.md` 7.1）。
- `specan_mixer`：`conversion_loss`（已有）、`iip2_dbm`/`iip3_dbm`/`p1db_dbm`、`image_rejection`（dB，缺省无穷大）、`lo_feedthrough`（dBm，调谐于 0 Hz 附近出现的线）、`spurs`（`[[m, n, dBc], …]`，m·f_rf ± n·f_lo 的响应）、`residuals`（`[[频率 Hz, dBm], …]`，无输入也有的线）。
- `specan_if`：`iip2_dbm`/`iip3_dbm`/`p1db_dbm`（中频放大器）。
- `specan_lo`：`phase_noise`（`[[偏移 Hz, dBc/Hz], …]`，对数-对数插值、端点按表延伸）。

迹线上的效果：失真产物由链的级联截点生成（`engine/rfcascade.py`，最强的 64 条输入线参与，略去的在保真度标注里记「近似」）；增益压缩按 P1dB 的三阶模型作用于各输入线；镜像与杂散按抑制比/dBc 折算成显示谱线；相位噪声按载波功率 × L(偏移) × 噪声带宽进检波器的噪声统计。解析求值覆盖以上全部；采样式求值（`evaluation: sampled`）只含平坦度、镜像与杂散（它们已在模拟谱线里），覆盖说明中写明。

数据手册的指标按频段给出时（噪声系数、截点、压缩点），参数用 2.3 的 `table` 映射按中心频率取值：表项是频段的下沿，取不大于中心频率的最大表项；手册没有给的频段写 `null`（理想），不外推。迹线的级联指标本来就在中心频率处求一次，所以这与求值的方式一致（谐波、交调产物在别的频段时用的仍是中心频率所在频段的值）。例（N9320B，#960）：

```yaml
iip3_dbm:
  from: $freq.center
  table: {0: null, 300.0e6: 13.0}     # 300 MHz–3 GHz +13 dBm nominal；300 MHz 以下手册没有给
```

### 2.11 信号链：采样流的块与连线（#961）

仪表内部的功能块之间有两种连接（D67，设计见 `signal-chain.md` 第 2–5 节）：

- **模拟块**：端口介质 `signal`，覆盖 `output(port)`，传递解析信号（直流 + 谱线）。频谱仪前端的 `specan_input`、`specan_mixer`、`specan_if` 还是 `RfStage`：给出各自的增益、噪声系数与截点，级联在 `engine/rfcascade.py` 里算。
- **数字块**：端口介质 `samples`，覆盖 `frames(port, request)` 与 `sample_interval(port)`，按帧拉取采样（`engine/stream.py` 的 `FrameRequest` → `SampleFrame`；`kind` 为 `volts | codes | iq | bins`）。现有的通用数字块：`specan_ddc`（数字下变频，输出 `iq`）、`specan_rbw`（加窗 FFT，输出 `bins`）；示波器的 `scope_decimator`。
- **ADC 是两域的边界**：`adc`（通用，固定满量程）输入 `signal`、输出 `samples`；示波器用 `scope_adc`（满量程跟随垂直设置）。数字域没有回到模拟域的块。

连线照常写在 `wiring`（`[源.端口, 目标.端口]`），定义校验按介质检查：

| 写法 | 诊断代码 | 原因 |
| --- | --- | --- |
| `samples` 输出接到 `signal`/`electrical` 输入 | `E-DEF-WIRE-MEDIUM` | 采样流只能接采样流输入 |
| `signal` 输出直接接到 `samples` 输入 | `E-DEF-WIRE-MEDIUM` | 两者之间必须有 ADC |
| `ports` 把采样流端口映射为仪表端口 | `E-DEF-WIRE-MEDIUM` | 采样流只在仪表内部传递 |
| `samples` 输入没有连线 | `E-DEF-SAMPLES-OPEN` | 采样流输入不能悬空 |
| 依赖调谐的端口（`mixer.out`、`if_filter.out`）映射为仪表端口 | `E-DEF-PORT-TUNED` | 它的输出取决于本振调在哪里 |

**最小的完整例子**：一台简化的数字化仪，输入端口 → 放大 → `adc` → 数字下变频，全部是现有的通用组件。文件在 `tests/definitions/doc-digitizer.yaml`，`tests/test_docs_examples.py` 检查下面这段与文件相同，并加载运行（−20 dBm 的 10 MHz 单音放大 20 dB 后，`ddc.out` 的复幅度为 0.316 V；上面每一条拒绝都各有一条用例）：

```yaml
# instrument-authoring.md 2.11 的例子（#961）：一台简化的数字化仪，全部是现有的通用组件。
# 输入端口 → 放大（specan_input 的前放，带噪声系数）→ adc → 数字下变频（specan_ddc）；
# 读数由内部探针读 ddc.out（采样流端口不能映射为仪表端口）。tests/test_docs_examples.py 加载运行。
schema: simubench.instrument/1
id: doc-digitizer
manufacturer: SimuBench
model: DOC-DIG-1
class: digitizer
description: 文档例子：输入 → 放大 → ADC → 数字下变频，采样流的块与连线。
state:
  dig.gain:
    type: float
    unit: DB
    min: 0.0
    max: 40.0
    default: 20.0
  dig.nco:
    type: float
    unit: HZ
    min: 0.0
    max: 50.0e6
    default: 10.0e6
components:
  - name: rf_in
    type: input_port
    params: {z_in: 50.0}
  - name: amp                  # 模拟块：signal 进、signal 出，是一级 RfStage（增益与噪声系数）
    type: specan_input
    params: {att: 0.0, att_auto: false, preamp_gain: $dig.gain, preamp_noise_figure: 6.0}
  - name: adc                  # 边界：signal 进、samples 出
    type: adc
    params: {bits: 14, full_scale: 1.0, rate: 100.0e6}
  - name: ddc                  # 数字块：samples 进、samples 出
    type: specan_ddc
    params: {nco_hz: $dig.nco, decimation: 100}
wiring:
  instrument:
    - [rf_in.out, amp.in]
    - [amp.out, adc.in]
    - [adc.out, ddc.in]
ports:
  instrument:
    IN: rf_in.pin
commands:
  - {header: ":DIGitizer:GAIN", state: dig.gain}
  - {header: ":DIGitizer:NCO", state: dig.nco}
```

采样流端口不能映射为仪表端口，所以这台仪表没有 SCPI 读数：`ddc.out` 由内部探针读取（signal-chain.md 1.3、第 6 节；Web 的内部结构图点开端口，或 MCP / HTTP 的 internal probe）。要给出 SCPI 读数，需要一个把采样流合成为读数的块——频谱仪里是 `specan_detector` / `specan_trace`；没有现成的块时写插件（下文）。

**宏展开后的样子**：现有定义不必改写。加载时宏把采样流补上（`library/macros.py`；Web 的“展开后的定义”与 `DefinitionFiles.expanded` 显示的就是它），作者写的条目位置不变，新增的排在表尾：

- 示波器（`scope_acquisition` 没有写 `decimator` 时，`sb-dso-4` 为例）——增加 `scope_decimator` 块 `dec`，原来的一条连线 `[ch{n}.probe.out, acq.in{n}]` 拆成三条：

  ```yaml
  components:
    # … 作者写的 acq、timebase、trigger、adc（scope_adc）、meas、xfer 不变；acq 增加 decimator: dec
    - {name: dec, type: scope_decimator, params: {adc: adc, horizontal: timebase}}   # 新增
  wiring:
    instrument:
      - [ch{n}.probe.out, adc.in{n}]     # 原来接 acq.in{n}
      - [adc.out{n}, dec.in{n}]          # 新增
      - [dec.out{n}, acq.in{n}]          # 新增
  ```

- 频谱仪（`specan_trace` 没有写 `rbw` 时，`sb-sa-1` 为例）——增加 `adc`（16 位、±1 V、100 MSa/s，类别默认）、`specan_ddc`（`nco_hz` 取 `if_filter` 的 `if_hz`）、`specan_rbw`（RBW 的设置与耦合从 `specan_if` 移来），视频滤波与迹线改为读 `rbw`：

  ```yaml
  components:
    # … rf_in、ref_in、clock、input、mixer、lo、if_filter、detector、vbw、sa 不变；vbw 与 sa 增加 rbw: rbw
    - {name: adc, type: adc, params: {}}                                          # 新增
    - {name: ddc, type: specan_ddc, params: {}}                                   # 新增（if_hz 写出时 nco_hz 取它）
    - {name: rbw, type: specan_rbw, params: {rbw: $bw.res, rbw_auto: $bw.res_auto}}  # 新增，参数从 if_filter 移来
  wiring:
    instrument:
      - [rf_in.out, input.in]
      - [input.out, mixer.in]
      - [mixer.out, if_filter.in]
      - [ref_in.out, clock.ref]
      - [if_filter.out, adc.in]          # 新增
      - [adc.out, ddc.in]                # 新增
      - [ddc.out, rbw.in]                # 新增
  ```

**两种求值方式**（频谱仪，`specan_trace.evaluation`，signal-chain.md 5.4）：`analytic`（默认）由谱线与级联指标解析地画迹线，2.10 的全部非理想都在；`sampled` 让迹线真的由 `adc → ddc → rbw` 的采样流产生，平坦度、镜像、杂散在（它们已在模拟谱线里），相位噪声与失真产物、压缩没有，窄 RBW × 宽跨度超出 `MAX_REQUEST_FANOUT` 时报错而不退回解析求值。两种方式共用同一批块与参数；解析求值的仪表的采样流同样可以用内部探针看。覆盖表里怎样写见 `real-models.md` 第 6 节。

**插件组件的 `frames`**（第 4 节的插件出口）：带 `samples` 输出端口的组件类覆盖两个钩子——`frames(port, request) -> SampleFrame` 与 `sample_interval(port) -> float`（读者不带速率时，例如探针，按它请求）；有 `samples` 输入的块用 `self.input_frames("in", request)` 取上游的帧（经会话的帧缓存，沿链计入 `MAX_REQUEST_FANOUT`）。帧必须是请求、参数、上游与种子的纯函数，不在调用之间保存状态；要历史的块向上游多要（`request.upstream(倍数, 预热)`）并丢掉对应的输出；自己加噪声时随机数键取 `stream_key(self.session.seed, *request.key, self.uid, "<流名>")`（`Component.frames` 的说明）。一个数字增益块：

```python
from dataclasses import replace

from simubench.engine.component import Component, PortSpec, component


@component("sample_scale")
class SampleScale(Component):
    """A digital gain: every sample of the stream times ``factor``."""

    ports = {"in": PortSpec("in", "samples"), "out": PortSpec("out", "samples")}
    defaults = {"factor": 1.0}

    def frames(self, port, request):
        up = self.input_frames("in", request)
        return replace(up, data=up.data * float(self.param("factor")))

    def sample_interval(self, port):  # the same rate as its input
        src = self.owner.wiring.source(self.name, "in")
        return self.owner.components[src[0]].sample_interval(src[1])
```

`tests/test_docs_examples.py` 运行这一段：把它接在上面例子的 `adc` 与 `ddc` 之间（`factor: 2`），`ddc.out` 的复幅度加倍。

### 2.12 设置之间的约束（#1013，D69）

手册常写“A 取某值时 B 的某些取值不可用”（DSO-X 2000 的 `:ACQuire:TYPE`：分段存储下没有平均），或“改 A 时仪表自动改 B”（coupling）。定义顶层的 `constraints` 逐条声明：

```yaml
constraints:
  - when: {acquire.mode: SEGMented}        # 条件：设置等于某值，或属于某几个值（写成列表）；几项同时成立
    forbid: {acquire.type: [AVERage]}      # 条件成立时不允许的取值
    action: refuse                         # refuse（默认）：整条命令被拒绝，什么都不改
    error: -221                            # refuse 的错误码，默认 -221 Settings conflict
    source: "Programmer's Guide 02.66, :ACQuire:TYPE, p. 215"
  # action: coerce 时写 coerce: {acquire.type: NORMal}：改动照常生效，被禁止的设置改成这个值
```

- **按改动后的状态判断**，与先设哪个无关：先 SEGMented 再设 AVERage、先 AVERage 再切 SEGMented，都违反同一条。一条命令同时写几个设置（参数绑定多个设置的命令、`writes`）时，按这条命令全部写完后的状态判断，中间状态不算；被拒绝时一个都不写（与 #550 的全有或全无一致）。
- **哪些写入受约束**：来自仪表外面的写入——SCPI 命令直接写的设置（命令的 `state`、绑定设置的参数、`writes`）与界面、MCP 的设置（`configure`）。仪表自己的写入不检查：组件在操作里或作为连带（`on_state`）写的设置，不论是否 `internal`（#1030；所以经组件操作写设置的命令，例如 `:CHANnel<n>:PROBe`，不受约束，要受约束就让命令直接写设置）；运行记录回放与故障注入也不检查。实现上只有 `CommandRunner` 与 `Session.configure` 以 `set_state(..., outside=True)` 写入，组件调用 `set_state` 默认不检查。
- **只用整机设置**（`state`），不用按通道的设置（`channel_state`）；数值关系（“采样率 × 存储深度 ≤ 上限”）第一版没有，以后加在 `when` / `forbid` 里。
- **静态校验** `E-DEF-CONSTRAINT`：引用的设置存在、取值是它的选项或在范围内；`coerce` 的值本身不是被禁止的取值；开机（`default`）与 `*RST` 后（`rst`）的状态不违反任何一条。`coerce` 只与 `action: coerce` 同用，并且为每个被禁止的设置给出一个值（模型校验）。
- 手册没写错误码或没写另一个方向怎样时，选定的做法与错误码在覆盖表里注明为假设。

## 3. 通用波形传输与采集（G2、G3）

### 3.1 `waveform_transfer`

已实现（#43，`library/components/transfer.py`）：sb 示波器、Siglent SDS 与低频校准参考示波器的数据回读都改为本组件的配置，回复与改动前逐字节一致。与下面的设计稿的差别：文本的前后缀不放在组件里，由命令的 `reply` 模板加上（`'{header} " {value} "'`，操作命令的应答也走模板，随 `COMM_HEADER` 回显或省略命令头）；ADC 码由参数描述：`codes_per_div`、`offset`（`subtract` 或 `add` 记录的偏移设置）、`adc_bits`（限幅）、`width`（8 或 16 位，按位移对齐）、`unsigned`、`byteorder`，都可以绑定或映射设置（例如 sb 的 `:WAVeform:FORMat` 同时决定编码、码值比例与字宽）；`block_digits`（如 Siglent 前导的 `#9`）、`trailer`（块后的换行）、`indefinite`（`#0` 加数据到消息末尾的不定长块，可绑定设置，Infiniium 的 `:WAVeform:STReaming ON`，#602）。“当前记录是哪一个”（读取开始时是否重新采集）由采集组件的 `transfer_record` 回答，留给 #44。这条缝已声明化（#630）：`record` 是一个协议引用，采集组件须实现 `engine.component` 的 `Acquisition` 协议（`read_record`、`transfer_record`、`timebase_scale`、`acq_count`），定义加载时随 refs 校验（缺方法在 E-DEF-COMPONENT-REF 里点名），命令运行时访问器再查一次；前导描述要用的输入链（探头比、ADC、每格码数）是可选的 `AcquisitionInput` 协议，采集组件没有实现就落回传输自己的参数。

一个通用组件，读取采集组件的记录，按配置编码。低频校准的文本回读写成：

```yaml
- name: xfer
  type: waveform_transfer
  params:
    record: acq
    first: $waveform.fp
    count: $waveform.np            # 0：到末尾，受 max_points 限制
    step: $waveform.sp
    max_points: 30000
    encoding: text
    text: {format: "%.6e", separator: " ", prefix: '{header} " ', suffix: ' "'}
```

`encoding` 取 `text`（分隔的数值，可加前后缀）、`ascii`（逗号列表）、`block`（IEEE 488.2 定长块：`dtype`、`byteorder`、块头位数，可选块后附加字节）。数值可选 `volts`（物理量）或 `codes`（ADC 码，需配合 `adc`）。Siglent WAVEDESC 这类二进制描述头仍由型号组件生成（结构太专用），但数据块部分改用本组件。

### 3.2 可配置的采集组件

把 `ScopeAcquisition`、`SiglentSdsAcquisition`、`LecroyAcquisition` 合成一个可配置的 `scope_acquisition`（第 8 节又把它拆成时基、触发、ADC 组等模块，这里的一个组件写法作为宏保留）：

已实现（#44，`library/components/scope.py`）：三台示波器（sb、Siglent SDS、低频校准参考示波器）的记录长度、触发、运行控制、噪声与 ADC 都写在定义里，回复、虚拟时间与采集事件与改动前一致（只有 Siglent 平均采集的耗时按 D46 改为 N 次）。`siglent_sds_acquisition` 与 `lecroy_acquisition` 只剩专用命令（WAVEDESC、`:TRIGger:STATus?`、依赖其他设置的范围、`INSPECT?`、`PAVA?`）。与下面的设计稿的差别：参数是平铺的（与 `waveform_transfer` 一致，每项都可以绑定或映射设置）：

- 记录长度：`record_length`（`fixed`：`points` 点覆盖 10 格；`exact`；`rate_ladder`：按 1-2-5 向下取档；`rate_pow2`：最高采样率除以 2 的幂向下取档，#602）、`points`、`max_rate`；`sample_rate` 大于 0 时（可绑定设置，如 Infiniium 的 `:ACQuire:SRATe`）采样率取它向上的下一档、优先于存储深度，记录为采样率 × 10 格点，`max_points` 限制记录点数（超出时采样率向下取档）；同一 ADC 的通道对 `rate_pairs` 同开时为 `max_rate_paired`；`delay` 绑定时触发点在中心之前 `delay` 秒；
- 触发：`trigger_source`、`trigger_level`、`trigger_slope`，按通道的设置写作 `$ch{n}.trigger_level`（`{n}` 是触发通道号，静态校验同样检查），`rising`、`falling` 列出两个沿的取值；非通道的触发源（外触发、电源）不触发；
- 运行控制：`mode` 与 `auto`、`normal`、`single`、`stop`、`force`（强制一帧后停止，Siglent 的 FTRIG）各自的取值，`after_single`（为空时模式不变、停止运行），`reacquire`，`missing`（`acquire`：没有记录的通道读取时按当前信号采一次，`error`：报 -230），`untriggered`（未触发的采集耗时：整个触发搜索或一帧）。等待中的单次采集在每次读取时重试。通用的 op：`run_mode`、`single`、`run`、`stop`、`force_trigger`、`wait`、`opc`、`digitize`；
- ADC：`adc_bits`（0：不量化）、`adc_codes_per_div`（0：`2**bits / adc_divs`）、`adc_offset`；
  - 偏移符号 `offset_sign`（`scope_adc`）与传输的 `offset`（`waveform_transfer`）各留一份，理由（#667）：码值内核 `adc.codes` 只有一份，两处只各自声明「设定的偏移加到还是减去」这一厂商约定；传输码是输出格式的属性（`sb-dso-*` 的 BYTE/WORD、Siglent 的 8/10 位左对齐用不同的 `codes_per_div`、`adc_bits`、`width`），可以与 ADC 的显示码不同，所以不由 ADC 推出；两处写成不同值是错误（`E-DEF-OFFSET-CONVENTION`，仅在 ADC 量化且传输发码值时检查；两个类的默认值不同，一处没写会被查出来）。理由与该检查记在 #634 的评论里。
  - 调制谱线预算：RF 信号源的 `LINE_BUDGET = 999` 是金标准锁定的（`tests/test_rfgen_migration.py` 复现的参考值），AWG 调制源的 `line_budget` 是参数（默认 2000），两者是不同仪表类别的限值，不合并。
- 噪声：`noise_rms`、`noise_key`（`time` 时 `noise_stream` 绑定的设置选择另一组噪声，如采集方式）、`trigger_jitter_s`，`averaging` 与 `averages`。

原设计稿：

```yaml
- name: acq
  type: scope_acquisition
  params:
    record_length: {points: $memory.size, rule: exact}     # exact | rate_ladder（按 1-2-5 取采样率）| rate_pow2
    max_rate: 2.0e10
    trigger: {source: $trigger.source, level: "$ch{source}.trigger_level", slope: "$ch{source}.trigger_slope",
              rising: [POSitive], falling: [NEGative]}
    run: {mode: $trigger.mode, auto: [AUTO], normal: [NORMal], single: [SINGLE], stop: [STOP],
          after_single: STOP, reacquire: on_read_start}      # on_read_start | every_read | explicit
    adc: {bits: 12, span_divs: 10}
    noise: {rms: 2.0e-4, key: acquisition}                  # acquisition | time（D46 的做法）
```

迁移：Siglent 与 LeCroy 的组件改为这份组件的配置（另加各自专用的 op，如 WAVEDESC），由现有手册用例与验收测试保证行为不变。

## 4. 插件出口（G4）

无法声明的行为用插件组件实现，接口与内置组件相同（`Component`、`@component`、`@op`、`OpResult`、`Signal`），并带版本号 `plugin_api: 1`。两种来源，信任程度不同：

| 来源 | 加载条件 | 说明 |
| --- | --- | --- |
| 已安装的 Python 包，入口点 `simubench.components` | 默认加载 | 与安装任何 Python 包同等信任；适合团队共享的组件库 |
| 工作区 `components/*.py` | 默认**不**加载；`simubench serve --allow-plugins`，或工作区设置中显式开启 | 工作区可能来自 git 检出或他人共享，执行其中代码需要工程师明确同意 |

- MCP 不提供写入 `.py` 文件的工具；Agent 需要插件时，生成说明与草稿交给工程师（原则 3）。
- 运行记录头部带插件文件的哈希，回放时不一致则警告（与定义哈希相同，D36）。
- `simubench doctor` 列出已加载的插件及来源；诊断包包含插件清单（不含源码）。

## 5. 编写与验证工具（G5–G8）

### 5.1 定义的静态校验（G6）

`simubench validate` 同时接受定义文件；MCP `validate_definition`。检查：格式（JSON Schema）；组件类型存在；`op` 名存在于该组件类型；状态绑定与 `reply` 模板引用的路径存在；内部连线与端口映射指向存在的组件端口；命令头可解析、没有两条命令匹配同一命令头；参数类型与绑定状态的类型相容。诊断沿用测试台的结构（错误码 `E-DEF-*`、JSON 指针、建议）。

已实现（#40，`engine/validate_definition.py`）；#42 加入了应答模板、多状态命令与参数映射的检查；MCP 工具随 #46 加入。错误码：

| 代码 | 内容 |
| --- | --- |
| `E-FORMAT`、`E-SCHEMA` | 格式版本、JSON Schema（与测试台相同） |
| `E-DEF-COMPONENT-UNKNOWN`、`E-DEF-COMPONENT-DUPLICATE` | 组件类型不存在；组件名重复 |
| `W-DEF-PARAM-UNKNOWN` | 组件没有这个参数（警告：不会起作用） |
| `E-DEF-STATE-UNKNOWN` | 组件参数或命令绑定的状态不存在；通道状态（`ch.`）用在没有通道号的地方 |
| `E-DEF-PARAM-TYPE` | 组件参数与绑定状态的类型不相容（数值、布尔量接受枚举：组件读取 `FIFTy`、`OFF` 这样的名称） |
| `E-DEF-PARAM-VALUE` | 取词参数（组件的 `choices`，如 `smu_meter.function` 只认 VOLTage/CURRent/RESistance）写了不认识的词，或绑定的状态可取其他值（字符串状态、枚举里有多余的选项，#308） |
| `E-DEF-OP-UNKNOWN` | 组件或操作不存在；通道组件的操作用在没有 `<ch>` 的命令头上 |
| `E-DEF-WIRE-COMPONENT`、`E-DEF-WIRE-PORT` | 内部连线、端口映射指向不存在的组件或端口；由连线创建的端口（`in1..inN`）由组件的 `port_pattern` 声明 |
| `E-DEF-PORT-TUNED` | 把取决于调谐点的端口（组件类的 `tuned_ports`：频谱仪混频器、中频滤波的 `out`）映射成仪表端口：仪表之外没有调谐点（#952） |
| `E-DEF-WIRE-MEDIUM`、`E-DEF-SAMPLES-OPEN` | 采样流（`samples` 端口，D67）的连线：采样流输出接到了不是采样流的输入；模拟信号输出直接接到采样流输入（中间必须有 ADC）；把采样流端口映射成仪表端口。采样流输入没有连线接到它（不能悬空，#946） |
| `E-DEF-REPLY`、`E-DEF-DIALECT`、`W-DEF-MAP-KEY` | 应答模板引用不存在的状态（或没有 `state` 的命令用 `{value}`）；`echo_header` 绑定的不是 OFF/SHORT/LONG 枚举或布尔设置、`long_form` 绑定的不是布尔设置；参数映射的表中有设置取不到的值（#42） |
| `E-DEF-DEVIATION`、`W-DEF-DEVIATION-ZERO` | 个体偏差指向不存在的组件或参数、非数值参数；按 % 或 ppm 的相对偏差用在标称值为 0 的参数上（不起作用，#34） |
| `E-DEF-DEVIATION-BANDS` | 随频率变化的曲线参数（`specan_input.flatness`）的个体偏差没有按频段写（`bands`），或 `bands` 用在不是曲线的参数上（#1004） |
| `E-DEF-DEVIATION-WHEN` | 按设置分组的个体偏差（`deviations` 的 `when`，D70）引用的设置不存在或是按通道的设置、取值不是该设置能取的值，或同一参数有两条同样条件（或两条都不带条件）的偏差（#1020） |
| `E-DEF-CONSTRAINT` | 约束（`constraints`，2.12）引用的设置不存在或是按通道的设置、取值不是该设置能取的值、`coerce` 的值本身被禁止、开机或 `*RST` 后的状态违反约束（#1013） |
| `E-DEF-OPTION` | 选件重复、选件的 `params` 指向不存在的组件、命令的 `option` 没有在 `options` 中声明（#1004；选件参数本身按组件参数的规则检查） |
| `E-DEF-HEADER`、`E-DEF-HEADER-DUPLICATE` | 命令头无法解析（例如 `[1]` 不是节点）；后写的命令被先写的命令匹配了某种写法，永远不会执行 |
| `E-DEF-PANEL-STATE`、`E-DEF-PANEL-CONTROL`、`E-DEF-PANEL-VALUE` | 面板（`display.panel`，7.3）引用的设置不存在；控件与设置的类型不相容（`switch` 用于布尔、`select` 用于枚举、`number` 用于数值，数值或字符串的 `buttons` 要写出 `keys`）或设置只读；按键、软键、`sets` 与 `when` 的取值不是可选项、超出范围或类型不符（#128） |
| `E-DEF-WRITE` | 命令 `writes` 的常量不是该设置的选项、类型不符或超出范围（#117） |
| `E-DEF-PANEL-READING`、`E-DEF-PANEL-ACTION`、`W-DEF-PANEL-IGNORED` | 读数不是 `<组件>.<读数>` 或组件不存在；软键的面板动作既不在 `display.actions` 中也没有采集组件执行；有专用屏幕的类别声明了面板（不使用） |

### 5.2 组件元数据（G7）

组件类型声明它在电路中的角色，测试台校验据此判断，不再写死组件名：`role: driver`（驱动源）、`role: series`（串联、阻抗可忽略，端接判断穿过它）、`impedance_of(params)`（已为输入端口实现）。内置组件补齐声明；插件组件同样可以声明。

已实现（#40）：组件类上的 `role`；驱动源是 `role: driver` 组件的电气输出端口（`out` 或 `inout`）。内置声明：`output_stage`、`unity_buffer`、`dc_supply` 为 driver，`coax_cable` 为 series；`smu_output` 不是 driver（到限值时吸收电流，与电源接在同一网络是合法的第二象限测试台）。

### 5.3 组件目录（G5）

`GET /api/components` 与 MCP `list_components`：全部组件类型（含内部组件），每个给出端口、参数（默认值、单位、说明）与 op（名称、参数、说明），由类的声明与文档字符串生成。`get_definition` 补充组件与连线。

已实现（#41，`server/catalog.py`）：参数的说明是源码中 `defaults` 每项旁边（或紧挨其上）的注释，op 的参数取自方法签名（`query`、`channel`、`suffixes` 由仪表传入，不列出），说明取自文档字符串；另给出电路角色与按连线创建的端口（`port_pattern`）。MCP 的 `list_components` 不带 `types` 时返回摘要（第一行说明、参数名与 op 名），指明类型时返回全部字段，避免一次返回整个目录（约 50 KB）。目前约六分之一的参数写了注释，其余在改到对应组件时补上。

### 5.4 MCP 编写工具（G5）

| 工具 | 内容 |
| --- | --- |
| `list_components` | 5.3 |
| `create_definition` / `edit_definition` | 写入工作区 `instruments/`；编辑以 JSON Patch 表达，带读取时的哈希（乐观并发，D25）；每次保存进入版本历史（D35），标为 Agent |
| `validate_definition` | 5.1 |
| `draft_definition_from_script` | 在 `draft_bench_from_script` 的静态分析之上，为模型不支持的命令生成命令与状态量草稿（类型按参数推测，标为待确认） |
| `run_cases` | 5.5，返回每条用例的结果 |
| `list_templates` / `get_template` / `create_definition_from_template` | 7.4：用数据手册的语言填写模板的输入，展开为定义 |

工具共享现有的限流与 `guard`；不写代码文件。

已实现（#46，`server/definitionfiles.py`、`engine/jsonpatch.py`、`engine/scriptscan.py` 的 `draft_definition`）：

- `read_definition` 返回文件内容、`sha256` 与诊断；工作区之外的定义（内置、搜索路径上的）`editable: false`，要修改就以新 id（或同 id，工作区的文件优先）`create_definition` 一份副本。
- `create_definition` / `edit_definition` 写 `instruments/<id>.yaml`；没有通过校验的内容也保存（与测试台相同，进行中的工作不丢），诊断随结果返回。`edit_definition` 的补丁整体应用或整体不应用；`sha256` 过期时报冲突并提示先 `read_definition`。每次保存进入版本历史（`.simubench/history/instruments/<id>.yaml/`，来源 `agent`）。
- JSON 中没有无穷大：定义里的 `.inf` 在 MCP 中写作 `"INF"`（与测试台文件相同），保存后照常加载。
- `draft_definition_from_script`：对脚本中的一台仪器（多台时用 `receiver` 指定），在 `base_definition`（不给时为空骨架）之上，为它不支持的每个命令头生成状态量与命令：数值（带 V、HZ、S 等单位）、ON/OFF、单词（枚举）、带引号（字符串）；多个参数的命令生成多个状态量与绑定参数的命令；只查询的命令为只读字符串。起草的条目在 `label` 与 `doc` 中标为“草稿（待确认）”，并列在返回的 `drafted` 中。f-string 的占位符按 `1` 读取，所以循环里的通道号只看得到 1，通道数需要 Agent 自己改。就地把扩展存回同一份工作区定义（`base_definition = definition_id`）时，带上刚读到的 `base_sha256`，一次调用完成保存（#169）；不带 `base_sha256` 的 `save=True` 仍是首建。
- **在模板定义上补命令**（#169）：Agent 先 `draft_definition_from_script`（`base_definition` = 定义自身，`save=True` 带 `base_sha256`）覆盖保存；之后若用 `create_definition_from_template(base_sha256=...)` 按模板重新展开，平台先用文件里记录的 `template.inputs` 再展开一遍，与现有内容比较——发现模板之外的改动（起草的命令、`edit_definition` 的修改）时拒绝展开，列出这些条目的 JSON Pointer，由 Agent 把改动并入模板输入，或用 `discard_edits` 确认丢弃。文件没有 `template` 记录、或记录的是另一个模板时，不比较，行为同从前。
- 保存后，使用该定义旧版本的会话列在结果的 `sessions_to_restart` 中；会话信息的 `stale_definitions` 同样列出，界面顶栏显示“仪表定义已修改”，点击即重置（按新定义重建）。

### 5.5 用例运行命令（G8）

`simubench cases <cases.yaml> [--junit 报告]`：在刚上电的测试台上逐条运行用例（与 `tests/test_real_models.py` 同一个运行器，移入 `library/coverage.py`），退出码与 JUnit 报告供 CI 使用（D6）。真实型号的手册用例与按契约编写的契约用例用同一种文件。

已实现（#45）：失败时给出步骤、命令、预期与实际回复，退出码 1（文件读不出为 2）；`manual` 改为可选（真实型号的覆盖检查仍要求每条用例写），步骤可用 `instrument` 发给测试台中的另一台仪器；MCP `run_cases` 返回每条用例的结果。运行器经引擎接口收发（不再经 PyVISA 后端），回读一条消息的全部字节，二进制块包括其后的换行。`examples/lowfreq-cal/cases.yaml` 是第一份契约用例，它查出了 #43 引入的一处回归（`FP` 超出记录时回复 `C1:INSP "  "`，多一个空格），已在应答模板中修正：值为空时只留一个空格。

## 6. 分期

| issue | 内容 | 依赖 |
| --- | --- | --- |
| #40 | 定义的静态校验（5.1）与组件角色元数据（5.2）（已完成） | — |
| #41 | 组件目录接口与 MCP `list_components`（5.3）（已完成） | — |
| #42 | 声明式应答：方言、应答模板、`pairs` 参数、倍率后缀、参数映射（第 2 节）（已完成） | #40 |
| #43 | 通用波形传输组件（3.1）（已完成） | #40 |
| #44 | 可配置的采集组件，Siglent 与 LeCroy 改为配置（3.2）（已完成） | #43 |
| #45 | `simubench cases` 与 MCP `run_cases`（5.5）（已完成） | — |
| #46 | MCP 编写工具：创建、编辑、校验、由脚本生成定义草稿（5.4）（已完成） | #40、#41、#45 |
| #47 | 插件出口：入口点与工作区插件（第 4 节） | #40 |
| #48 | 验收：只用 YAML、再只用 MCP 重建低频校准工作台（第 0 节）（已完成） | #42、#43、#44、#46 |

界面中的定义编辑不在本期：定义文件仍可用文本编辑器与 MCP 修改，界面的“查看内部结构”（issue 20）显示结果。第二期见第 7 节。

## 7. 第二期：Agent 搭建，Web 验收与配置（设计稿，#123）

### 7.0 分工与衡量

定义文件的 YAML 是平台的内部格式，用户不需要了解它。分工如下：

| | 做什么 | 不做什么 |
| --- | --- | --- |
| **MCP（Agent）** | 搭建：根据手册、脚本或用户的描述，创建和修改仪表定义与测试台；自己试运行、跑用例、按诊断改正 | — |
| **Web（用户）** | **验收**：看懂 Agent 搭出的仪表（面板、功能与量程、命令、端口、内部结构），试着操作，跑用例，看懂每次修改改了什么；**配置**：像操作真实仪表的面板一样设置仪表 | 不编辑 YAML，不提供定义的编辑器或表单 |

测试台的画布编辑（添加、连线、配件）不涉及 YAML，照常保留在 Web。

**衡量标准**：用户只对 Agent 说一句需求（例如“照这份手册节选做一台万用表，接到信号源上”），Agent 只用 MCP 完成搭建；用户只用 Web、不打开任何文件，完成验收：

1. 在仪表的面板上看到并设置功能、量程、NPLC、输入阻抗，读数随之变化，与手工计算一致；
2. 在验收页看到它支持的功能与量程、命令列表，以及每次修改的说明（“新增命令 `:SENSe:VOLTage:NPLC`”“直流量程增加 1000 V”），而不是 YAML 的差异；
3. 在验收页运行 Agent 写的契约用例，全部通过；一个 PyVISA 脚本读到正确读数。

定标等验证场景用来检验这些能力，不在本期的产品范围内。

### 7.1 现状与缺口

| # | 缺口 | 证据 |
| --- | --- | --- |
| W1 | Web 中看不懂一台仪表：只有检查器的设置列表和只读的内部结构图（#20）。看不到它支持哪些功能与量程、哪些命令，也没法在不建测试台的情况下试 | `Inspector.tsx`、`internals/` |
| W2 | Web 看不懂 Agent 改了什么：定义的版本历史只在文件中（`.simubench/history/instruments/`），界面没有入口；测试台的差异审阅（D35）按字段显示，对定义来说是 YAML 结构，不是仪表的语言 | `edit/HistoryDialog.tsx` 只处理测试台 |
| W3 | 万用表、校准源等没有真机式面板：没有专用类别模板的仪表用通用布局，即读数加设置列表 | `screens/ReadingsScreen.tsx` |
| W4 | Agent 搭建要从组件写起：万用表要懂 `dmm_input_terminal`、`dmm_conditioning`、`dmm_adc`、`trigger_model`、`calibration_store` 的参数与绑定，34465A 的定义约 500 行，出错多、轮次多 | `library/instruments/keysight-34465a.yaml` |
| W5 | Agent 没法单独试一台仪表：要先建测试台、开会话；`run_cases` 只跑写好的用例 | `mcp_server.py` |

### 7.2 Web：仪表验收页（W1、W2）

从检查器、资源树或首页的“仪表”列表打开，是工作台的一个标签页，与“内部结构”并列。全部只读，内容都用仪表的语言：

- **概览**：身份（厂商、型号、`*IDN?`）、类别、端口与介质、参考的手册版本；功能与量程表（由定义中的状态量与组件参数整理，例如“直流电压：100 mV、1 V、10 V、100 V、1000 V，超量程 120%”）；技术指标（`specs`）与个体偏差的限值，并排显示；
- **命令**：按命令树列出命令头、参数（类型、单位、范围、默认值）、查询回复的格式、说明；真实型号另显示覆盖表中的实现程度（实现 / 接受 / 未实现）；
- **内部结构**：#20 的视图；
- **试运行**：在只含这台仪表的临时会话上发命令、看回复与错误队列，可以用面板（7.3）操作；临时会话与打开的测试台互不影响，不写运行记录；
- **用例**：列出工作区中针对它的用例文件，一键运行，逐条显示步骤、预期与实际；
- **修改记录**：定义的版本历史（D35），每个版本由来源（Agent、外部修改）、时间和**变更说明**组成。变更说明由平台比较前后两版生成，用仪表的语言描述：命令的增删改、状态量（设置）的增删与范围或默认值变化、量程与组件参数变化、个体偏差与技术指标变化。可以对比任意两版。

验收页不提供编辑。发现问题时，用户把问题告诉 Agent，由 Agent 修改；页面随文件变化自动刷新（与测试台的外部修改载入相同）。

**T4 的实现（#129）**：

- 页面在 `/i/<定义 id>`（首页“仪表”列表、检查器的“验收页”），也可以作为工作台标签页与“内部结构”并列；六个分页只读。
- 内部结构接口（`GET .../internals`、`GET /api/try/{sid}/internals`）除 `wires`（定义里的信号连线）外还返回 `refs`（#729）：组件在 `refs` 参数里点名的另一个组件，方向是“被使用的 → 使用它的”（采集使用 `adc`、时基、触发，测量与波形传输使用采集）。它由组件类型自己声明的 `refs` 推导，不是 `wiring:`，不改定义格式，也不改信号通路；图按它排列列，让被使用的阶段排在使用者之前，没有任何连线与 `refs` 的组件（系统块、状态寄存器）归入“命令 / 状态侧”。
- 内部结构接口的每个组件还带三份清单（#739，`server/internals_links.py`）：`settings`（它的参数跟随的设置，按通道解析，如 `ch1.scale`）；`commands`（驱动它的 SCPI 命令，每条带 `setting`（它写的那个设置）或 `op`（它运行的操作）：`op:` 指向它的操作，或经 `state:`、参数的 `state` / `keys`、`writes` 写它跟随的设置；`measure.p{slot}.kind` 这类按任意槽匹配；通道组件的命令头填好通道号，如 `:CHANnel1:SCALe`）；`readings`（它自己的与它到达的组件提供的查询命令，每条带 `provider`（实际组件名）、`path`（从本组件到提供者的最短组件链）、`via`（最后一步：`self` / `wire` / `ref` / `select`）、`terminal`（提供者不再供给别的组件，即仪表的结果读数：波形数据、测量，而不是路上组件自己的状态）与 `selected_by`（通道组件经过一个按设置选通道的组件时，那个设置）。到达先走信号连线与 `refs`，再走选通：通道经采集到达波形传输，`waveform.source` 只决定读哪个通道；只有别处到不了的（按 `trigger.source` 选源的触发）才以 `select` 到达。接口顶层的 `selects` 列出按设置选通道的组件与那个设置。宏在定义加载时已展开，反查对展开后的命令与组件做，不需要来源信息。界面的交互见 `ui-design.md` 第 6 节“内部结构图”。
- 读接口：`GET /api/definitions/{id}/document`（sha256 与静态校验诊断，页面据此发现文件变化并自动刷新）、`GET /api/definitions/{id}/coverage`（覆盖表，没有时为 null）、`GET /api/try/{sid}/instrument` 与 `GET /api/try/{sid}/internals`（临时会话的同一份仪表：概览、面板、内部结构、试运行都读它）。T2 的试运行与用例接口、T3 的修改记录接口照旧。
- 概览的“功能与量程”用仪表实际显示的面板（定义的 `display.panel`，或类别默认布局）整理；没有面板的类别退回设置表。命令页把定义的命令与覆盖表合并，真实型号显示“实现/接受/未实现”。
- 自动刷新：轮询定义的 sha256；变化后重取定义与覆盖表、重开临时会话，并在页头提示“已载入新的修改”。工作区的第一版按 D35 记为“外部修改”（保存前文件的内容），Agent 之后每次保存各有说明。
- 端到端测试 `web/e2e/instrument.spec.ts`（用例、试运行、Agent 保存后的刷新、工作台标签页入口）。页面未加入 Storybook，视觉基线不变。

### 7.3 Web：真机式面板（W3）

用户配置仪表用面板，与操作真实仪表一样：

- 面板由定义生成。定义的 `display` 增加面板布局的声明：分组（例如“测量”“量程”“触发”）、每个设置用什么控件（按键组、旋钮式数值、开关、下拉）、主显示区显示哪些读数与单位、软键。Agent 经 MCP 写这部分，与写命令一样；
- 没有声明面板时，按类别给出默认布局：万用表为大号读数加功能键、量程键；校准源为输出值、工作 / 待机键。都没有时退回现在的通用布局；
- 已有专用屏幕的类别（示波器、函数发生器、锁相放大器、频谱仪、SMU）保持不变；
- 搭建模式下，面板设置的是测试台文件中的初始设置；操作模式下是运行中的设置（#103 的说明照常显示）。脚本连着时遵守远程状态（#121）。

面板只改设置，不改仪表定义。

已实现（#128）。定义中的写法（`library/panels.py` 生成类别默认布局，`web/e2e/fixtures/instruments/sb-panel-1.yaml` 是一份完整的声明）：

```yaml
display:
  panel:
    readouts:     # 主显示区：读数 <组件>.<读数>、单位、位数；when 为显示条件
      - {reading: meter.DC, label: 直流, unit: V, digits: 6, when: {sense.function: DC}}
    groups:       # 分组，每组若干控件
      - label: 量程
        controls:
          - state: sense.range
            control: buttons          # buttons | number | switch | select
            keys:                     # 按键：写入的值，sets 为同时写入的其他设置（全部成立时按键点亮）
              - {value: 1, label: 1 V, sets: {sense.range_auto: false}}
              - {value: 0, label: AUTO, sets: {sense.range_auto: true}}
          - {state: sense.nplc, control: number, step: 0.5}   # number 可带步进键与显示单位
    softkeys:     # 软键：面板动作（run / stop / single），或把一个设置写为某个值
      - {label: 精确, state: sense.nplc, value: 10}
```

枚举的按键组不写 `keys` 时每个选项一个键，布尔量为开、关两个键。没有声明时，万用表由 E1（#190）拆出的测量链生成：读数取 `dmm_adc` 的值，功能与按 `ranges` 的量程键（加 AUTO，量程设置有 `AUTO` 特殊值、自动量程绑定了布尔设置时）取 `dmm_conditioning` 的绑定，输入端子、调理与 A/D 绑定的其他设置；校准源由 `calibrator_output` 生成：输出值、交流时的频率、输出端状态，功能键与幅度、频率，OPER / STBY 键；DAQ 由 `scan_table` 生成：刚扫描的通道与读数、扫描表、闭合通道，功能与 NPLC、扫描次数与间隔，以及 Run / Stop 软键；电源由每路的回读、保护状态、输出开关与电压/电流设定，以及输出 2/3 的组合键生成。会话接口的 `display.panel` 给出界面要画的面板（`panel_source` 为 `declared` 或 `class_default`）；专用屏幕的类别与没有默认布局的类别为空，界面用专用屏幕或通用布局。

### 7.4 MCP：模板与试运行（W4、W5）

**仪表模板**：供 Agent 使用。Agent 用数据手册的语言填写一组输入，平台生成组件、状态量与命令，Agent 不必从组件写起。以万用表为例，输入包括：

- 身份：厂商、型号、`*IDN?` 格式；
- 功能与量程：每个功能的量程、超量程比例与读数；
- 分辨率与读数时间：按 NPLC 列表；
- 输入阻抗：标准值、可选的高阻值及其适用量程；
- 触发与读数存储、校准（安全码、分键）；
- 命令方言：通用 SCPI 万用表命令集与可选变体；
- 面板布局：类别默认，可以覆盖；
- 技术指标与个体偏差：分开填写，不自动换算（D3）。

模板展开为一份普通定义，文件中记下 `template: {id, version, inputs}`。Agent 之后可以改输入再展开，也可以直接改展开后的内容。不采用“定义引用模板、运行时合并”：那样会多出一层格式，模板升级还可能悄悄改变已有仪表的行为。这条格式选择与 7.0 的分工一起记入 D3。

模板由平台提供，每个是 `library/templates/` 中的一个生成函数，加上一份输入的 JSON Schema；不引入表达式语言。模板生成的是第 8 节的模块组合，所以哪一类先定下组件粒度，哪一类先做模板：第一批是示波器，万用表、函数发生器、校准源随后。

| 工具 | 内容 |
| --- | --- |
| `list_templates` | 模板摘要（类别、说明、版本） |
| `get_template` | 输入的 JSON Schema 与一份示例输入 |
| `create_definition_from_template` | 按输入展开并保存；输入的诊断指向 `template.inputs` 下的字段 |
| `try_definition` | 在只含这台仪表的临时会话上依次发送命令，返回每条的回复、新增的错误与虚拟时间；不需要测试台，不写运行记录；同一定义的连续调用复用同一个临时会话（会话与虚拟时间延续，直到闲置或被淘汰），并发批次依次执行；验收页的「重新开始」是替换式新开（7.2），HTTP 的试运行请求与批次同样互斥 |

**T1 的实现（#125）**：

- `library/templates/`：每个模板是一个 `Template`（`id`、`version`、类别、中英文名称、说明），带一个输入的 pydantic 模型（导出 JSON Schema，每个字段有 `title`、`title_en`、`description`，有单位的带 `unit`）、一份示例输入、生成函数 `build` 与检查函数 `check`。`check` 做 schema 表达不了的检查（顺序、覆盖、引用），诊断码 `E-TPL-*`，指针在 `/template/inputs` 下；输入有错时不生成定义。
- 展开得到普通定义，`template: {id, version, inputs}` 原样记下输入；`InstrumentDefinition.template` 只是记录，加载与运行时忽略它（去掉它，仪表的行为不变）。
- 示波器模板（`oscilloscope`，版本 1）的输入：身份；通道（数量、输入阻抗的关键字与电阻电容、带宽与响应形状、带宽限制、耦合、上电显示的通道）；垂直（档位范围、按 V/div 递增的档位表：偏置范围、噪声、增益误差限与偏置误差限，超范围报错或钳位，探头比的范围与换算，偏置 DAC 误差限）；ADC（位数、每格码值、格数、最高采样率、交织映射与成对时的采样率、偏置方向）；时基（范围、记录长度列表、存储深度、参考位置、时基误差限）；触发（触发方式、触发沿、外触发、电源触发、迟滞、电平范围、抖动）；采集（方式、平均次数、同步规则）；自动测量；波形传输（格式、每格码值、字节序、无符号）；命令方言（通用 SCPI 示波器命令集，`:SINGle` 为采集一次、布防或没有，可选 `:RUN` / `:STOP`）；技术指标。个体偏差的范围写在档位表与时基中，与技术指标分开（D3）。
- 响应形状：`first_order`（默认，模拟前端）、`brickwall`（平坦幅度）、`gaussian`、`bessel4`、`butterworth4`，对应 `scope_vertical.response`（#607、#609）；频响表要先有组件能力。
- 给了存储深度时，记录按存储深度与 1-2-5 采样率取得，`:WAVeform:POINts` 的点数在记录上均匀抽取，`:WAVeform:PREamble?` 用 `waveform_transfer` 的声明式前导描述实际发送的点；不给时记录就是点数设置，前导用 `scope_acquisition` 的通用前导（与 `sb-dso-4` 相同）。
- MCP：`list_templates`、`get_template`（schema 与示例）、`create_definition_from_template`（输入有错时返回诊断、不保存；保存进入版本历史，来源 `agent`；给出 `base_sha256` 时按新输入重新展开、覆盖已有文件）。
- 验收（`tests/test_templates.py`、`tests/test_mcp.py`）：示例输入展开后没有诊断，只用通用组件；按 `sb-dso-4` 的数据填写的输入生成的示波器，对验收场景的 56 条命令（34 条查询，32 条有回复）的回复与 `sb-dso-4` 逐字节相同；示例接入信号源后，按前导重建的波形与记录一致、测量与触发正确、`:SINGle` 在虚拟时间中完成；个体偏差随种子复现且在档位表的限值内；十个错误输入各有诊断，指针指向输入字段。

**信号源的组件粒度与模板（#144，T6 的函数发生器部分）**：

- 信号源拆成模块：`waveform_synth`（1 Vpp 的标准波形，频率改变时相位连续；方波、脉冲、斜波、三角波的谐波到 `bandwidth` 为止，超出谱线上限时带保真度说明，D33）→ `amplitude_level`（幅度与偏置，单位 VPP / VPK / VRMS / DBM，按负载设定定义，D13）→ `output_stage`（输出阻抗、开关）；`reference_clock` 给出合成频率的误差（内部 ppm，或锁定 REF IN 上 ±10 ppm 内的 10 MHz，锁不上时用内部）。`sb-awg-2` 与低频校准工作台的校准仪改用这些模块，用例不改通过；`function_generator_core` 保留给用它写成的定义。
- 组件的 `optional_refs`：引用其他组件的参数中可以不填的（不填即没有，例如没有参考时钟的信号源）。
- 引用的解析（#382）：通道组件引用的名字先找本通道的 `ch<n>.<名>`，再找仪表级的同名组件（`Component.peer`）；所以整条链可以放进 `channel_components`，每个通道各自一条（双通道 SMU `sb-smu-2`），单通道定义照旧。静态校验同样先查本通道、再查仪表级。`*TRG` 触发所有带 `bus_trigger` 的组件：没在等总线触发的被跳过，没有一个接受时报第一个的错误（单个触发模型的行为不变）。
- **dBm**：Vrms = √(10^(dBm/10) · 1 mW · R)，R 为负载设定；负载设定为高阻（`inf`，或不小于 `hiz_from`）时幅度是开路电压，dBm 按 `dbm_hiz_ohms`（50 Ω）换算。各波形的峰值与有效值之比沿用原有换算（正弦 2√2、方波与脉冲 2、斜波 2√3 Vpp/Vrms）。
- **占空比按声明读写**：`waveform_synth` 的 `duty_unit` 为 `percent` 或 `ratio`；状态量的 `ratio`（“大于 1 的值按百分数读”）取消，写了它的定义会得到说明如何改写的错误。低频校准仪的占空比是比例：`0.001` 即 0.1 %，`40` 超出范围（-222）。
- **信号源模板**（`signal_source`，版本 1）：身份、通道数、波形（命令关键字与形状）、频率（范围、方波与斜波上限、谐波带宽）、幅度（负载设定下 Vpp 口径的范围（D48）、单位、显示名；上电值按上电单位填写）、偏置、相位、占空比（表示与范围）、负载设定（范围、高阻关键字、高阻阈值）、输出（阻抗与误差限、开输出是否需确认）、参考时钟（时钟源关键字、误差限、参考输入）、命令方言（`scpi-source`：`[:SOURce<n>]:FUNCtion`、`:VOLTage`、`:OUTPut<n>`；`channel-base`：`:CHANnel<n>:BASE:*` 树、`:LOAD`、`:OUTPut`、`:SYSTem:CLKSource`；未知命令报错或忽略）、技术指标。多单位（或首单位不是 VPP）时幅度状态没有静态范围，范围由 `amplitude_level` 的 `vpp_min` / `vpp_max` 检查，切单位换算数值（D48）。
- 验收（`tests/test_signal_source.py`）：按 `sb-awg-2` 的数据填写的输入生成的信号源，对一串命令的回复与 `sb-awg-2` 相同，接 50 Ω 负载时输出相同；按校准仪的数据生成的信号源替换低频校准工作台的校准仪后，契约用例全部通过；VPP、VRMS、dBm 在 50 Ω 负载设定下与负载上的实测一致，高阻设定下 dBm 按 50 Ω 换算；0.1 % 的脉冲在 10 kHz（谱线上限截断，带说明，高电平时间约 100 ns）与 5.5 MHz（截到信号源自己的带宽，谱线与解析傅里叶系数一致，无说明）；比例占空比的 0.001 不被读成 10 %；参考时钟的个体偏差随种子复现并进入输出频率；八个错误输入各有诊断。

**由脚本选模板并预填输入（#145）**：

- 每个模板声明自己的命令集变体（`variants`：每个变体一份打开全部功能的输入）。变体展开后的定义就是它的命令特征：扫描脚本时，与选择内置定义一样用 `engine/scriptscan.py` 的 `rank` 计数，命中最多的变体胜出，依据是它支持的命令与行号。示波器模板有 `scpi-scope` 与 `lecroy-scope` 两个变体，信号源模板有 `scpi-source` 与 `channel-base`。
- 示波器模板的 `lecroy-scope` 命令集：`C<n>:VDIV`、`TDIV`、`MSIZ`（记录恰为该点数）、`TRSE`、`TRMD`（`SINGLE` 后变为 `STOP`）、`PACU` / `C<n>:PAVA?`、`WAVEFORM_SETUP` 与 `C<n>:INSPECT?` 的文本回读（`max_points` 为每次读取上限）、`COMM_HEADER` 回显命令头；耦合关键字 `D1M`、`A1M`、`D50`、`GND` 同时说耦合与输入阻抗。另加输入：档位与时基是否按 1-2-5（`ladder`）、存储深度设定、自动触发等待的记录数、`scpi-scope` 的 `:WAVeform:POINts:MODE`、未知命令报错或忽略。低频校准工作台的参考示波器与校准仪都可以由模板写出，契约用例全部通过。
- **预填**（模板的 `prefill`）：从脚本中能读出的值填进输入，每项记下来源——`script` 带行号（如 `*IDN?` 校验的型号串取自 `--scope-model` 的默认值或 `*_MODEL` 常量），`default` 为待确认的默认值。占空比按脚本写的值推断表示：都不大于 1 时按比例并提示待确认，有大于 1 的值时按百分数。f-string 的占位符读作 `1`，所以循环里的通道号看不出来，通道数按脚本中最大的通道号与变体的默认值取大者，并标为待确认。
- `draft_bench_from_script`：内置库没有完整支持某台仪器的命令时，报告的 `template` 给出模板、变体、定义 id、`inputs`（`create_definition_from_template` 直接接收）、`filled`、展开后仍不支持的命令（行号）；测试台草稿中这台仪器的型号就是该定义 id。只有一台示波器与一台信号源时，按两边脚本中最先用到的通道给出直连线缆（`inferred_wires`，标为推断）；运行方式取确定性的默认（种子 1、`clock: command`、`idle: off`）并说明。只发公共命令（如 `*IDN?`）的资源看不出是哪台仪表，不加入草稿。
- 验收（`tests/test_template_draft.py`）：低频校准示例脚本只用内置库与模板，选出 `lecroy-scope` 与 `channel-base`，预填输入展开后没有诊断，脚本的全部命令被支持；按草稿创建两份定义、用草稿测试台运行脚本，1 kHz–100 kHz 的幅值为 1 Vpp（±2 %）；类 Keysight 脚本在没有对应型号时选出 `scpi-scope`、全部命令被支持，有内置型号时直接选型号；加一条模板没有的命令时按行号报告；MCP 协议级测试从 `draft_bench_from_script` 到两份定义与 `validate_bench`。

Web 验收页需要的读接口（7.2）由 HTTP 提供：定义的概览与命令表、修改记录与变更说明、试运行与用例运行。HTTP 不提供写定义的接口。

### 7.5 本期不做

- Web 中编辑定义（YAML、表单、组件图都不做）：修改一律经 Agent；
- 用户自写模板；
- 由技术指标自动推出个体偏差（D3 不采用）；
- 定义的共享与资产库。

### 7.6 分期

| issue | 内容 | 标签 | 依赖 |
| --- | --- | --- | --- |
| T1 | 模板机制与示波器模板，MCP 模板工具（7.4） | 核心 | 第 8 节示波器的拆分 |
| T2 | MCP `try_definition`；HTTP 的临时会话试运行与用例运行（7.2、7.4） | 核心 | — |
| T3 | 定义的变更说明：比较两版，生成仪表语言的说明；HTTP 的修改记录接口（7.2） | 核心 | — |
| T4 | Web 仪表验收页：概览、命令、内部结构、试运行、用例、修改记录（7.2） | 核心 | T2、T3 |
| T5 | 面板布局的声明（`display`）与校验；Web 按声明生成面板，万用表与校准源的类别默认布局（7.3） | 核心 | — |
| T6 | 模板输出面板布局；万用表、函数发生器、校准源各自先定组件粒度（第 8 节），再做模板（各一个 issue） | 核心 | T1、T5 |
| T7 | 验收：7.0 的完整流程。Agent 只用 MCP 搭建（协议级测试），用户只用 Web 验收（端到端测试） | 可认领 | T1、T4、T5 |
| T8 | 为已有真实型号（34465A、5522A）补面板布局的声明 | 可认领 | T5 |
| T9 | 用户文档：让 Agent 搭仪表，在 Web 中验收与配置 | 可认领 | T4、T5 |

T1、T2、T3、T5 可以同时开始。

## 8. 组件粒度（设计稿，从示波器开始）

### 8.1 为什么

D1 的设想是：仪表由功能模块组合而成，误差、校准常数放在它们真实所在的模块上，任一模块的输出都可以替换（例如用录制的波形替换 ADC 的输出，D14）。现在各类别的粒度不均：

| 类别 | 现在的组件 | 粒度 |
| --- | --- | --- |
| 示波器 | `input_port` → `scope_frontend` → `scope_acquisition` → `waveform_transfer`；型号专用的 `siglent_sds_acquisition`、`siglent_sds_channel_limits` | 前端已经拆开，采集仍是一整块 |
| 万用表 | `dmm_input_terminal`（端子与输入阻抗）→ `dmm_conditioning`（检波、量程、模拟误差）→ `dmm_adc`（量化、校准修正、读数）+ `trigger_model`、`reading_buffer`、`calibration_store` | 只有电压功能 |
| DAQ | `daq_backplane`（插槽与模块）+ `mux_card`（继电器与模拟总线）→ 内部 DMM（E1 的测量链）+ `scan_table`、`trigger_model`、`reading_buffer` | 首波固定 34970A + 34901A；每通道测量配置与可配置插卡在缺口清单 |
| SMU、频谱仪、射频源、校准源、负载、电源 | 各一个组件 | 一整块 |

粗粒度的代价：

- 拼不出不属于现有类别的仪表；
- 误差放不到具体的级上；
- 内部没有可以替换的中间信号；
- 加功能只能继续扩大组件。

好处是连线少，不容易出错。所以目标不是把所有东西拆碎，而是：

1. **拆成标准模块**：信号在模块之间经端口流动，每个模块对应仪表里一个真实的功能块，参数用数据手册的语言；
2. **大组件保留为宏，在加载时展开**：已有的组合照常可用，参数不变；加载定义时展开为标准模块，展开后的形式是唯一的实现（与第 7 节模板的“展开为普通定义”同一原则），不另走一条运行时代码路径；
3. **型号专用组件清零**：能声明的都改为声明，#93 的例外清单只减不增。

模板（第 7 节）生成的是标准模块的组合，所以每一类先定粒度，再做模板。第一个是示波器：它的前端已经拆开，又有一台真实型号（SDS2104X Plus）可以检验拆分是否够用。

### 8.2 示波器：现状

`scope_acquisition`（约 600 行）一个组件包了：

- **水平**：时基、记录长度规则（fixed / exact / rate_ladder / rate_pow2）、可设置的采样率、采样率上限与通道配对；
- **触发**：边沿触发的源、电平、斜率，以及触发抖动；
- **垂直**：每个通道的刻度、偏置、探头比，从状态量直接读取；
- **ADC 与噪声**：位数、每格码值、偏置的加减；输入端白噪声；
- **运行控制**：AUTO / NORMAL / SINGLE / STOP / FORCE 的状态机，何时重新采集，平均；
- **自动测量**：命名的测量；
- **命令操作**：`digitize`、`*OPC`、`wait`；前导信息是 `waveform_transfer` 的 `header` 声明（操作 `xfer.header`）。旧写法的 `op: <acq>.preamble` 加载时按宏展开为 `xfer.header`，并给传输模块补上 Keysight 十字段 `header`（#665）。

两个型号专用组件只为 Siglent 写：

- `siglent_sds_acquisition`：`:TRIGger:STATus?` 的状态文字、346 字节的 WAVEDESC 二进制头、`:ACQuire:TYPE` 及其次数、探头比改变时随之缩放刻度与偏置、只读的采集参数（点数、采样率、采集次数、最大点数）、`:MEASure:SIMPle:VALue?`；
- `siglent_sds_channel_limits`：刻度与偏置的范围随输入阻抗、探头比和刻度变化。

### 8.3 示波器：保真度的缺陷

一次专业评估（以资深仪表开发工程师的视角，对照代码与实测）指出，下列缺陷会让脚本在虚拟环境中通过、在真机上失败，或者测出错误的数值。拆分要把这些问题的解决放进各模块的职责，否则拆完仍是一台“理想 + 同步”的示波器：

| # | 缺陷 | 依据 |
| --- | --- | --- |
| F1 | **采集是同步的**：单次采集在命令内部完成，读取会顺带完成等待中的采集，没有触发时 `*OPC?` 也回 1，`:DIGitize` 不阻塞。“没等触发就读数据”“依赖 `*OPC?` 同步”“没有信号时脚本挂死”都暴露不出来 | `scope.py` `run_mode`、`read_record`、`opc`、`digitize`；平台已有的待完成操作机制（`register_pending`）示波器没有用 |
| F2 | **自动测量算法错误**：AMPL ≈ 0.707×PKPK；FREQ 无迟滞，产生假过零；不足一个周期时不返回无效值 | `scope.py` `_measure`；#131 |
| F3 | **计量上是理想仪表**：没有每通道、每档位的增益与偏置误差，没有校准常数，时基没有 ppm 误差，通道时延无效；数据手册的指标只记录、不参与计算，也没有可以挂个体偏差的参数 | `siglent-sds2104xplus.yaml` 的 `specs` |
| F4 | **噪声不随档位变化，平均与高分辨率无法提高分辨率**：固定的输入端噪声，平均只把噪声除以 √N 再量化；PEAK / ERES 不改变数据 | D46；0.5 V/div 下 5 mV 直流平均后仍测得 0 |
| F5 | **“接受”用在了影响数据的命令上**（SKEW、参考位置、采集方式等） | 覆盖表；#132 |
| F6 | 触发在记录上按采样点搜索，不做亚采样插值；NORMal 等待不推进虚拟时间；`*RST`、改时基没有声明耗时；频响为一阶低通（#607 起可声明砖墙、高斯、4 阶 Bessel，#609 起加 4 阶 Butterworth）；削波后的测量仍当有效值 | `scope.py` `_trigger_search`、`ScopeFrontend` |

F2、F5 不依赖拆分，单独修（#131、#132）。其余随拆分解决。

### 8.4 示波器：拆分

信号路径：`input_port` → `scope_vertical` → ADC 组；触发另走一条模拟路径。

| 模块 | 内容 | 解决 |
| --- | --- | --- |
| `input_port`（每通道） | 输入阻抗、输入电容；可选的 50 Ω 过载保护（超过阈值切回 1 MΩ 并报型号的错误） | — |
| `scope_probe`（每通道，新） | 探头比与单位（V / A）；改探头比时刻度与偏置是否随之缩放；通道时延（SKEW，可声明个体偏差）。只做显示换算与时间偏移，不在模拟通路上 | F3、F5 |
| `scope_vertical`（每通道，取代 `scope_frontend`） | **以档位表为核心**：按 V/div（及输入阻抗）给出衰减器与增益档位、带宽与响应形状（一阶、高斯、频响表）、偏置范围、噪声（V rms 或格 rms）、**增益误差与偏置误差**（可声明个体偏差，可写入校准常数）、偏置 DAC 的增益误差、饱和范围。耦合与带宽限制保留。刻度与偏置的范围由同一张表给出，超出报 -222。顺序：耦合 → 衰减与增益 → 加偏置 → 饱和 → 带宽 | F3、F4、F6；取代 `siglent_sds_channel_limits` |
| `scope_adc`（共享的 ADC 组） | 位数、每格码值、原生采样率、通道到 ADC 的映射与交织（原 `rate_pairs`）；量化；录制码流替换的接口（D14，本期只留接口） | F4 |
| `scope_timebase`（共享） | 时基、延迟、参考位置（时间或百分比）、记录长度与存储管理规则、采样率上限；**采样时钟 ppm**（可声明偏差，可经 REF_IN 传递）；只读的点数与采样率 | F3、F5 |
| `scope_trigger`（共享） | **独立的触发路径**：在 ADC 之前的信号上检测，带自己的噪声；耦合（AC、低频抑制、高频抑制）、迟滞与噪声抑制、释抑；**亚采样插值**，给出触发时刻；外触发与电源触发作为端口或源。触发类型（`mode`）有边沿与脉宽（`glitch`，#956）；码型、欠幅等其余类型未做。状态文字与状态位按声明映射 | F6 |
| `scope_acquisition`（共享，缩小） | **运行控制与同步**：运行状态机改为声明式状态表；采集作为待完成操作（`register_pending`），状态随虚拟时间依次经过等待、已触发、停止；同步语义按型号声明——`*OPC?` 是否等待采集、等待中读取得到上一帧还是报错、没有触发时 `:DIGitize` 阻塞还是返回。**抽取与处理**：普通、峰值检测、平均、高分辨率，都在码值上做（先加噪声，再量化，最后处理）。死区时间、记录保存、`digitize`、只读的采集次数 | F1、F4 |
| `scope_measurements`（共享） | 参考电平（10 / 50 / 90 % 或绝对值）与迟滞、顶值与底值的求法（直方图或极值）、最少周期数、削波判定、门控、统计（次数、均值、σ、最小、最大）、无效值规则 | F2、F6 |
| `waveform_transfer`（共享） | 编码（不变）；**声明式的二进制头**（字段名、偏移、类型、字节序、取值来源），WAVEDESC 由此写成定义；浮点块（float32 / float64）；点数模式与按请求点数抽取；每次读取的上限与超限行为；亚采样的 xorigin | 取代 `siglent_sds_acquisition` 的传输部分 |
| 状态与事件寄存器 | 平台的 `system` 组件读取采集与触发模块的状态位（等待触发、运行、采集完成），型号声明位号 | 仪表开发者需要 |

模块之间通过参数中的组件名引用，与 `trigger_model` 引用 `sampler` 相同。

**宏**：`scope_acquisition` 照旧接受现在的全部参数。没有引用独立模块时，加载定义时按这些参数展开为上述模块；展开后与显式写出的定义指纹相同。

**采样流（D67、#950）**：`scope_acquisition` 的 `in<n>` 接的是采样流。完整的连线是 `ch{n}.probe.out → adc.in{n}`、`adc.out{n} → dec.in{n}`、`dec.out{n} → acq.in{n}`，其中 `dec` 是 `scope_decimator`（参数 `processing`、`averages`、`eres_bits`、`peak_max_oversample`，引用 `adc` 与 `horizontal`），`scope_adc` 用 `vertical`、`probe` 引用每通道的模块，采集用 `decimator` 引用它。定义只写了 `…probe.out → acq.in{n}`、没有写 `decimator` 时，宏在加载时补上这一段：生成名为 `dec` 的抽取块（加在组件表末尾，不改变已写组件的位置），把采集上的这四个参数移给它，把 ADC 的两个引用按采集的填好。

**暂不做**：数学通道与 FFT（放在测量之后，作为测量与传输的虚拟数据源）、数字通道、协议解码、参考波形、模板测试。分段存储见下（#957）。

**能否覆盖其他厂商**：按上表，Keysight 的 `:DIGitize` 阻塞与点数模式、R&S 的 `SING;*OPC?` 与 `FORM REAL,32`、Tek 的 `ACQ:STATE` 与 `WFMOutpre?` 的换算字段、Rigol 的分批读取上限与 `:TRIG:STAT?` 的取值，都可以用声明表达。这些厂商行为来自经验，未在仓库中核实，写进型号定义前要对照手册。

**G1 的实现（#133）**：

- 新模块：`scope_vertical`（`scope_frontend` 是它的旧名）、`scope_probe`、`scope_adc`、`scope_timebase`、`scope_trigger`。`scope_acquisition` 用参数 `horizontal`、`trigger`、`adc`、`vertical`、`probe` 按名称引用它们，引用由静态校验检查（`E-DEF-COMPONENT-REF`）。组件基类的 `refs` 声明“这个参数指向哪类组件”，以后的模块沿用。
- 宏在定义模型构造时展开（`InstrumentDefinition` 的前置校验调用 `library/macros.py`），所以加载文件、MCP、测试中直接构造定义都走同一条路。展开把模块的参数改用模块自己的名称，例如 `adc_bits` 改为 `bits`、`trigger_source` 改为 `source`；把接到 `<acq>.in{n}` 的每通道组件作为 `vertical`，并改用 `scope_vertical` 这个名称；再在它与采集之间插入名为 `probe` 的 `scope_probe`。名称冲突或连线不是这个形状时报 `E-DEF-MACRO`，要求把模块写出来。对宏形式的定义，校验诊断的指针指向展开后的位置。
- `scope_vertical` 的档位表本期只有刻度与偏置的范围（`scale_range`、`offset_ranges`，按输入端给出，用 `probe_ratio` 换算，超出报 -222 并保留旧值）。带宽、噪声、误差与饱和随 G3、G4 加入。
- 噪声、触发抖动与平均仍在 `scope_acquisition` 上：它们的随机流以采集组件的标识为键，搬动会改变每一个数值，而这两项的行为会在 G4、G5 中改变，届时再搬到 `scope_vertical` 与 `scope_trigger`。
- `sb-dso-4`、`sb-dso-2x` 改为显式写出模块；SDS2104X Plus 与低频校准参考示波器经宏展开，分别在 G6、G7 改写。

**G2 的实现（#134）**：

- `scope_acquisition` 的同步规则是四个参数：`completion`（`immediate` 为原有行为，`pending` 为在虚拟时间中完成）、`opc_waits`、`while_waiting`（`previous` / `error`）、`no_trigger`（`wait` / `return`）。默认 `immediate`，型号在定义中声明；SDS2104X Plus 声明 `pending`。`sb-dso-*` 与低频校准参考示波器保持 `immediate`。
- `pending` 只作用于一次性的采集：单次、强制一帧与 `:DIGitize`。运行中（auto、normal）每次读取重新采集的行为不变，相当于屏幕的刷新。安排采集时求出触发时刻与完成时刻，登记为待完成操作；没有触发时登记为“等待的事件可能不来”的操作，之后每条命令到达时再找一次触发（组件的 `retry_pending`）。
- 平台层：待完成操作可以声明 `*OPC` 不等它（`register_pending(..., waited=False)`）；`Instrument.block` 登记等不到事件的操作，内置与定义绑定的 `*OPC?` 在有这样的操作时不回复。
- 面板按键（D32）发起的采集当场完成：面板操作不推进虚拟时间，采集用的是面板自己的时间。面板留下的“等待触发”同样在之后每条命令到达时再找触发，触发到来后在虚拟时间中完成。
- 运行中的示波器切到单次时，当前这一帧作为最近一次采集保存，与真机屏幕上保留的那一帧一致：脚本在单次完成前读到它。
- 命令耗时用已有的 `duration`；`*RST` 可以绑定 `system.reset` 并声明耗时。SDS 的手册没有给出命令耗时，本期不编造数值。在 `idle: off` 或 `sleep` 下，不 sleep 的轮询脚本不会推进虚拟时间，要用 `time.sleep`、`*OPC?` 或墙钟（D47）。
- 依赖同步行为的用例按新语义改正：SDS 手册用例中“单次采集触发后停止”改为先 `Ready`、`*OPC?` 后 `Stop`；SDG 的测量与噪声验收在读数据前用 `*OPC?` 等待（步进时钟下显式推进时间）；原来“等待中的单次采集在之后的读取时完成”的测试改为“触发到来后采集在虚拟时间中完成，读取不会使它完成”。

**G6 的实现（#138）**：

- `scope_measurements`：从 `scope_acquisition` 搬出的测量。`names` 把型号的测量名对到引擎的测量与单位（`C1:PAVA? AMPL`、`:MEASure:SIMPle:VALue? PKPK`），op `named` 回复值并给应答模板 {kind}、{unit}、{status}；op `measure` 是 `:MEASure:VPP? [CHANnel1]` 的写法。规则都是参数：顶值与底值的求法（`top_base: histogram | extremes`）、计周期的电平（`mid`，百分比或 `levels: absolute` 的伏特）与迟滞（`hysteresis`，顶底差的比例，但不低于 `hysteresis_floor_divs` × 记录档位的格数——计数器的带按屏幕格取下限，纯噪声的迹线数不出跨越，测量回"不可测"，#745/#773；默认 0.16 格，是假设值而非手册值：编程手册与数据手册都不给计数器的最小幅度或迟滞带宽，未核实某真机的对应要求，`0` 关掉下限回到不带格下限的旧算法，#781）、最少周期数（`min_cycles`）、削波判定（`clipped: invalid` 时碰到 ADC 上下限的记录不可测）、门控（`gate`，记录中的格数范围）、无效值与状态字。每次测得的值进入统计，op `statistics` 给出次数、均值、σ、最小、最大，`clear_statistics` 清零。默认值与原来的算法相同，SDS、sb-dso 与低频校准示例的测量值不变。过冲/预冲沿用真机方法（InfiniiVision 2000 X 编程手册的 `:MEASure:OVERshoot`：极值减直方图顶底，除以顶底差）：含噪记录上极值带着几 σ 的噪声 excursion、直方图众数也向内偏移，干净正弦会读出噪声量级（≈ 最大样本的 4σ ÷ 信号幅度）的百分数——这是噪声的量级而非信号形状，与真机在同样记录上的读数一致（#782）；要看信号形状请用平均采集或带宽限制先把噪声压下去。
- 宏：测量写在采集上的旧写法（`measurements`、`invalid_value`、`status` 参数，命令的 `<acq>.measure` / `<acq>.measurement`）在加载时移到名为 `meas` 的 `scope_measurements`（名称被占用时依次为 `meas2`……），放在组件表末尾，所以与显式写出的定义指纹相同。
- `waveform_transfer`：
  - 声明的前导（`header`，op `header`）：二进制块按字节偏移写字段，或文本行按顺序写字段。字段取常量、记录与本次选点的数值（`figure`：点数、首点、间隔、字节数、采样间隔、首末点时间、刻度与偏置（显示的与输入端的）、探头比、ADC 位数、每格码数、时基、采集次数、通道号）或设置（`{n}` 为信源通道），再可经 `map`、`nearest`（按对数取最近的列表下标，如时基档位表）与 `scale`。Siglent 的 346 字节 WAVEDESC 由此写成定义；
  - 浮点块：`values: volts` 加 `float: 32 | 64`（R&S 的 `FORMat REAL,32`）；
  - 点数模式：`count_mode: spread` 在整条记录上均匀取 `count` 点；
  - 每次读取的上限：`max_points` 加 `over_limit`（`allow` 照旧、`truncate` 截断、`error` 报 -222）。
- 其余声明化：`scope_acquisition` 的 `status_text`（停止、等待触发、已触发、自动模式未触发的文字，op `status`）、`acquire_type` 与 `acquire_counts`（带次数的采集方式、允许的次数与查询格式，op `acquire_type`）、op `figure`（点数、采样率、采集次数）；`scope_trigger` 的 `level_range_divs`（电平在信源屏幕偏置附近的格数范围）；`scope_timebase` 的 `delay_range_divs`、`reference_mode` / `reference_position`（参考方式与触发位置）；`scope_probe` 的 op `set_ratio`（关键字与比值、比值范围，`rescales` 列出随探头比缩放的显示设置）。
- SDS2104X Plus 只用通用模块写成，`siglent_sds_acquisition` 与 `siglent_sds_channel_limits` 已删除。改写前后对同一串 105 条命令（前导块、波形数据、状态、采集方式、探头、测量、各项限值与它们的错误码）的回复逐字节相同。`sb-dso-4`、`sb-dso-2x` 显式写出 `meas`。
- 契约定义：`tests/models/contract-float-scope/` 是一台类 R&S 的示波器（float32 块、点数模式、文本前导、按名称的测量与统计），只用通用模块，用例全部通过。

**G3 的实现（#135）**：

- `scope_vertical` 增加 `gain_error_ppm`、`offset_error`、`dac_error_ppm` 与 `store`。前两项按**输入端**的 V/div 给：一个数，或 `deviations` 的 `by: {scale: …}` 画出的表（表里没有的档位没有刻画误差，贡献 0）。垂直模块在探头模块之前，所以增益误差沿输入通路乘上去、偏置误差按输入伏特加上；`dac_error_ppm` 是偏置 DAC 的增益误差，按**显示的偏置设定**折算（显示偏置 V 使记录偏 V·d），在这一级除以 `probe_ratio`，过探头后仍是显示域的值。误差的顺序：耦合 → 衰减与增益 → 加偏置 → 带宽。
- 校准常数：`scope_vertical` 的 `store` 指向 `calibration_store`，读数按 (原始 − 偏置) × 增益修正，键是**显示的** V/div（型号在存储上声明 `key: ["$ch.scale"]`）。`calibration_store` 新增 `calibrate`（自校准写系数：核心与自己的内部参考比较，不做输入容差检查，一次提交只让台账记一次）与 `state_key`（把单个 `$状态` 键按另一格的值解析，自校准因此能写当前不在用的档位）。`scope_vertical.self_calibrate` 为误差表里的每个输入端 V/div 写一组系数（标量：当前档），按当前探头比换成显示刻度；改探头比会移动显示刻度与键，新刻度从标称开始，需要再校准。
- `scope_probe` 增加 `skew`（绑定型号的通道时延）与 `skew_error`（本机的个体偏差，s）。采集在早于记录时刻 `skew + skew_error` 处取通道信号：这是 ADC 之后的数字延迟，触发仍检测模拟信号，不随时延移动。
- `scope_timebase` 增加 `clock_ppm`（本机采样时钟的频率误差）、`rosc`（INTernal / EXTernal）与 `ref` 端口。`clock_ppm` 为正表示时钟快，采样的真实间隔比标称的短，记录仍按标称 dt 重建，所以时间测量读数偏**低**（与频谱仪的参考约定一致）。EXTernal 时从 `ref` 上的 10 MHz 取得分数频率误差（±10 ppm 内、幅度 > 0.05 才算可用），参考消失或不可用时回到内部 ppm，与 `spectrum_analyzer`、`rf_reference` 的参考输入写法相同。引擎不在仪表之间路由 signal 介质的连线，所以 REF_IN 写成“`input_port`（如 50 Ω）+ 仪表内部连线到 `timebase.ref`”，与 `sb-sa-1`、`sb-lia-1`、`sb-sg-1` 一致。
- 自校准的命令：`scope_acquisition` 新增通用 op `self_cal`（每通道调用 `scope_vertical.self_calibrate`，回复诊断码 0），型号用 `reply` 模板给回复的文字。SDS2104X Plus 绑定 `*CAL`，回复 `*CAL 0`（SDS 系列编程手册的 SYSTEM *CAL?，0 = 成功）；EN11F 目录只列 `:SYSTem:SELFCal`、未列 `*CAL?`，覆盖表已注明来源与未核实。
- SDS2104X Plus（在 G6 的通用模块之上）：`front` 增加 `store: cal`，每通道 `cal` 存储以 `$ch.scale` 为键，`probe` 绑定 `skew: $ch.skew`。`deviations` 只写数据手册给出的两项：每个 V/div（1X，14 档）的直流增益 ±3 %（±30000 ppm）与时基 ±1 ppm；数据手册没有偏置误差与 SKEW 精度的指标，不编造数值（能力仍在组件上）。
- 验收：`tests/test_scope_acquisition.py` 覆盖每档的增益/偏置/DAC 误差（含探头比）、REF_IN 与回退、SKEW 与时钟 ppm 的个体偏差（`sb-dso-4` 上，与型号无关）；`tests/test_siglent_sds.py` 覆盖 SDS 的 14 档增益抽取在限值内且随种子复现、每档增益误差进入记录、`*CAL?` 写全部档位并使读数回到一个 ADC 码内、SKEW 使记录移相、时钟 ppm 在频率测量中可见，以及 5522A 的“核查 → 调整 → 复核”契约用例（`tests/models/siglent-sds2104xplus/calibration-cases.yaml`，与 `simubench cases` 同一运行器）；没有 `deviations` 的定义保持原有行为。

**G5 的实现（#137）**：

- 分段存储（#957）：`scope_acquisition` 的 `segments` 大于 1（且 `segmented` 为真，可绑定到“采集模式是分段”的设置）时，一次单次采集或 digitize 连续取这么多次触发，每次一条记录；一条记录采满后过 `rearm_s` 触发才重新布防（下一段还要先填满预触发时间），释抑从上一段的触发算起。它仍是一个待完成操作：最后一段采满才完成；触发在布防时按当时的信号找出，信号在完成之前变了，已经触发的段保留，其余的重新找；某一段的触发不来，采集就一直等（自动模式下该段按未触发取）。`segment_index`（从 1 起）选出读取、波形传输与测量使用的段；op `figure` 的 `segments` 是采到的段数，`time_tag` 是所选段相对第 1 段的触发时刻。`memory_points` 限定 段数 × 每段点数（超出 -221）。第 1 段的噪声键与不分段时相同，其后各段带段号，互相独立。
- `scope_trigger` 在 ADC 之前的模拟信号上检测，有自己的耦合滤波（`coupling` 与 `filters`：每种耦合一组高通、低通转折频率）和噪声（`noise_rms`）。先越过电平另一侧的迟滞带（`hysteresis_divs`，`noise_reject` 开启时为 `reject_hysteresis_divs`）才布防，然后在第一个越过电平的采样上触发，触发时刻在它与前一个采样之间线性插值。脉宽触发（`mode: glitch`，#956）用同一套过零时刻：一个 `polarity` 极性的脉冲从一次过电平到下一次反向过电平，这段时间与 `width_low`、`width_high` 按 `qualifier` 比较——`less`（小于 `width_high`）与 `range`（两者之间）在脉冲的结束沿触发，`greater`（大于 `width_low`）在脉冲持续到 `width_low` 的时刻触发（脉冲不必已经结束）；脉冲必须整个落在触发布防之后（预触发时间与释抑之内开始的脉冲不算），`slope` 不起作用。计算量按边沿远段的同一预算（`max_search_points` 按谱线数折算）：先在记录网格上取预算内的采样，再在所用脉宽界限 1/8 的网格上取同样多（更粗的网格会把中间夹着窄间隙的几个脉冲看成一个宽脉冲，#1000）；粗网格上的时刻在没有触发噪声时二分到千分之一采样，有噪声时在两个采样之间插值（误差不超过一格）；预算内找不到时普通采集继续等待、下一条命令再找。触发搜索是其输入的纯函数，同一时刻、同样的输入（状态寄存器在一行命令的每条之后都会问）直接用上一次的结果。
- 记录仍在采样时钟的网格上（数值与改动前相同）；触发落在两个采样之间的小数和触发抖动都进入记录的时间原点 `Record.origin`（xorigin），不再移动采样点。前导与描述中的 xorigin（sb-dso 的 `:WAVeform:PREamble?`、低频校准示例的 `HORIZ_OFFSET`、传输的 `x_offset` / `x_start` / `x_stop`）与示波器屏幕都用它。Siglent 的 WAVEDESC 照手册的时间轴公式只写延迟设置，不变。
- 自动模式只在 `search_windows` 个记录内找边沿（它的自动超时）；普通与单次采集在 `search_horizon_s`（默认 10 s）的虚拟时间内等边沿，所用时间计入采集：1 Hz 信号的单次采集在下一个边沿触发，记录填满后完成。超出首段的搜索按信号最高谱线的 1/16 周期取粗网格，跨越处在连续信号上二分到千分之一采样；总计算量按谱线数限制（`max_search_points`），电平超出信号可能的摆幅时不搜索。
- 释抑：`holdoff_on` 时，上一次触发后 `holdoff` 秒内的边沿不触发。电源触发（`line` 列出的信源）在 `line_hz` 的上升过零处触发；外触发（`external` 列出的信源）读 `ext` 端口，由定义把仪表的输入端接到它。
- 信源名 `CH1` 也识别为通道（原来只认 `CHANnel1`、`C1`）。
- 依赖“触发正好落在采样点上”的用例按真机行为改正：sb-dso 的触发点在采样 499 与 500 之间，xorigin 在 −5 ms 之后一个采样以内；低频校准示例的 `HORIZ_OFFSET` 同样。
- SDS2104X Plus 的触发耦合、噪声抑制与释抑命令没有在本期接上：这些需要按手册与数据手册核实的取值（转折频率、命令语法），留待能取得手册时再做。

**G4 的实现（#136）**：

- `scope_vertical` 增加 `noise_table`（[[V/div, 噪声 V rms], …]，输入端）：噪声随档位（F4、D46）。取当前档的行；档位没有列出时取不超过它的最大行的值（地板写在前几行即覆盖最灵敏的档位）。`scope_acquisition` 不再有 `noise_rms` 参数：σ 从通道的 vertical 模块取，按探头比折成显示伏特；随机流仍以采集组件为键（G1 的约定），`noise_key`、`noise_stream` 与触发抖动不变。低频校准参考示波器的假设噪声（与档位无关）写成单行表。
- `scope_vertical` 的 `noise_divs`（#731，默认 0 即无噪声）：没有 `noise_table` 时，输入噪声取“当前 V/div 的这一比例（V rms）”，随档位同比变化。示波器模板的 `vertical.noise_divs` 默认 0.02 格（约 8 位 ADC 的 0.6 个码值）：档位表给了噪声就以表为准，否则用它；写 0 才是无噪声输入。这是**假设值，不是某台真机的指标**：要还原一台真机，在档位表里按数据手册写每档噪声。触发抖动 `trigger.jitter_s` 不写时同样取假设值——最高采样间隔的十分之一（ADC 没给最高采样率时按 100 ps 假设，即一台 1 GSa/s 级示波器的十分之一采样间隔，同为假设值，#735），写 0 才是无抖动。已按旧默认展开的仪表不受影响（模板展开后不随升级，D3）；要加噪声，在它的定义里补 `noise_table` 或 `noise_divs` 与 `trigger_jitter_s`。
- 处理级 `processing`（`normal | average | peak | eres`，可按型号从采集方式映射），都在码值上做——先加噪声，再量化，最后处理（D46）：
  - `average`（`averages`）：N 次采集各自加噪声、量化，记录取均值，耗时 N 次。量化噪声随噪声一起被平均（方差 (σ² + q²/12)/N），所以平均能分辨一个码以内的信号——旧模型把噪声除以 √N 后量化一次，量化噪声一格不降，平均提高不了分辨率。平均的子采集沿噪声流顺延取值。
  - `peak`：每个记录间隔按 ADC 原生速率取样（`peak_max_oversample` 每记录样本至多 64 次，深记录慢时基才可行——真机的峰值检测器也是专用硬件），取间隔内最小与最大码中偏离间隔两端更远的那个进记录：记录速率会跨过的窄脉冲进得来。噪声取原生网格的流（键含原生 dt）。
  - `eres`：`4^eres_bits` 个码的居中 boxcar（边缘保留最近的完整窗），白噪声降 `2^b`——b 个有效位，带宽随窗变窄。
- 读数噪声（#746，D46；#750 扩展）：`noise` 参数（默认不写即无噪声），按读数有没有量程概念二选一：
  - 有量程（`dmm_adc`、`smu_meter`，数据采集的内部 DMM 同为 `dmm_adc`）：`{ppm_of_range, ref_time_s, table}`。`ppm_of_range` 是在参考积分时间 `ref_time_s`（默认 0.02 s，即一个 50 Hz 的 PLC）下占**当前量程**的 rms ppm，`table`（量程 → rms）覆盖它；rms 按 `sqrt(ref_time_s / 积分时间)` 随积分时间下降，积分时间取该组件的 `reading_time`（万用表）或 NPLC 折算的读数时间（源表）。
  - 没有量程概念（`supply_readback`、`eload_meter`、校准源回读 `calibrator_control`/`calibrator_output`）：`{rms: <数 或 {读数量: rms}>}`——绝对均方根（电源/负载按 V 与 A 各给，校准源按 V 给一个数），与 `ppm_of_range`/`table` 互斥；给映射时未点名的读数量无噪声。
  不带 `noise` 的组件：`mux_card`（继电器与模拟总线，没有测量读数；数据采集的读数噪声在内部 DMM 的 `dmm_adc` 上）。校准源回读噪声**只作用于 Web 面板读数**：校准源没有脚本侧的电压回读命令（脚本读输出电压靠接在输出上的万用表，噪声在万用表上），所以没有脚本/面板互不影响的问题（#796）。
  噪声加在校准（电路求解）之后、量化之前，所以末位会闪，高 NPLC 时稳定。写型号时的数值取数据手册，没有就写标明的估计值（`specs.noise_*`），并保证**明显小于该型号的准确度指标**，否则靠近限值的判定会翻转。脚本读数以该组件已发出的读数序号为键（`MEAS?` 不占虚拟时间，用时间区分不了），重放同样的命令逐位一致；Web 面板读数用自己的帧计数，不进脚本能看到的任何东西、不占虚拟时间。写错键名或取值（多余的键、负的 `ppm_of_range`/`rms`、非正的 `ref_time_s`、`table` 的键或值不是数、`rms` 与 `ppm_of_range`/`table` 同给）会在加载时报错（创建组件时抛 ValueError，`validate_definition` 报 `E-DEF-NOISE`），并列出允许的键（#752）。
- SDS2104X Plus：`processing` 由 `:ACQuire:TYPE` 映射，PEAK / ERES 由『存了不生效』改为实现。每档噪声为**估计值**（数据手册未给分档噪声，D46）：ADC 端约 0.015 格 rms（8 位痕迹约 3 码厚），折算到输入端 max(35.8 µV, 0.015 × 档位)——最灵敏的 0.5–2 mV/div 由 35.8 µV 的噪声底决定；数值写入 `specs.noise_per_division` 与 `front` 的 `noise_table`，覆盖表注明估计依据。
- `refcalc.noise`：`quantized_sigma`（一次量化的码分布，精确）不变；新增 `averaged_codes_sigma`（G4 的顺序：量化之后平均，量化噪声也随平均下降）。
- 验收（衡量 6）：各档噪声 σ 与档位表一致（`acceptance/test_noise_scenario.py`：噪声底的 0.5/1 mV/div 与估计的 5 mV、0.5 V/div）；0.5 V/div 下 5 mV 直流平均 64 次，MEAN 误差 < 0.2 LSB——旧模型平均后仍测得 0（F4 的实测）；ERES 2.0 的 σ 降到 1/4；1 kHz 的 500 ns 脉冲（源端谐波上限所限）在 NORMal 记录里成批漏掉、PEAK 全部保留；平均 / PEAK / ERES 记录的重放仍逐位一致。
- 依赖旧行为（极值上无噪声）的用例按真机行为改正：SDS 手册用例的 PKPK（2.00 → 2.0x）、5522A 例程的 MAX/PKPK/均值、SDG 负载测试的 PKPK、手册流程的偏差界——一次采集的极值本来就带几个码的噪声。

**G7 的实现（#139）**：

- 低频校准参考示波器改为显式写出模块：`timebase`（记录恰为 MSIZ 点）、`trigger`（每通道自己的电平与触发沿）、`adc`（12 位、±5 格、20 GSa/s）、每通道 `input_port` → `scope_vertical` → `scope_probe`（探头比走 `$ch.probe` 的默认绑定，`C<n>:ATTeNuation` 照旧），测量在 `meas`（`C<n>:PAVA?` 的测量名与单位）。改写前后的定义指纹相同；仓库里不再有经宏展开的定义，宏保留给用户已有的旧写法。以它为宏形式样本的两个测试（宏展开的诊断指针、测量搬移到 `meas`）改为从显式形式构造旧写法；`tests/test_lowfreq_cal.py`、`acceptance/test_lowfreq_cal_scenario.py`、`examples/lowfreq-cal/cases.yaml` 与 `tests/test_lfcal_mcp_rebuild.py` 不改通过。

### 8.5 示波器：衡量

**结构**：

1. `siglent_sds_acquisition` 与 `siglent_sds_channel_limits` 删除，SDS2104X Plus 只用通用模块写成；#93 的例外清单去掉这两项；
2. `sb-dso-4`、`sb-dso-2x` 与低频校准参考示波器改用独立模块；不改的副本经宏展开，指纹与显式写出的相同；
3. 内部结构图（#20）显示新模块与连线；`list_components` 中每个新模块的参数都有说明。

**保真度**（在 SDS2104X Plus 上，各有用例或测试；#151 起新能力以 DSO-X 2024A 为基准，D45）：

4. **同步**：单次采集后立即读数据，得到上一帧，采集次数不变；`:TRIGger:STATus?` 随虚拟时间依次为等待、已触发、停止；声明为阻塞的型号，没有触发时 `*OPC?` 或 `:DIGitize` 不回复，PyVISA 报超时；
5. **计量**：在 `deviation: from_specs` 的测试台上，各档位的直流增益误差在数据手册的限值内、随种子可复现；写入校准常数后读数随之改变；SKEW 与时钟 ppm 在时间测量中可见；用 5522A 与 SDS 写一份“核查 → 调整 → 复核”的契约用例全部通过；
6. **噪声与分辨率**：各档位的噪声 σ 与档位表一致；0.5 V/div 下 5 mV 直流平均 64 次后，MEAN 误差小于 0.2 LSB；
7. **触发**：多次采集的 xorigin 带亚采样偏移，边沿位置的统计抖动等于设定的抖动；1 Hz 信号在 NORMal 下，单次采集完成的虚拟时间等于下一个边沿；
8. **耗时**：`*RST`、改时基、改存储深度按声明的耗时推进虚拟时间。

现有的用例中，依赖旧行为（同步采集、理想增益、旧测量算法）的，按真机行为改正预期，在 PR 中逐条说明，并修改对应的决策条目。只追求回复逐字节相同，会把现有缺陷原样固化。

### 8.6 分期

| issue | 内容 | 标签 | 依赖 |
| --- | --- | --- | --- |
| #131 | 自动测量算法（F2） | 可认领 | — |
| #132 | “接受”只用于不影响数据的命令；SKEW 与参考位置（F5） | 核心 | — |
| G1 | 结构拆分：`scope_probe`、`scope_vertical`（档位表，暂不含误差）、ADC 组、`scope_timebase`、`scope_trigger`、缩小的 `scope_acquisition`；宏在加载时展开；`sb-dso-*` 改用独立模块（衡量 1–3 中除 SDS 外的部分） | 核心 | — |
| G2 | 同步语义：采集作为待完成操作，同步规则按型号声明，命令耗时（衡量 4、8） | 核心 | G1 |
| G3 | 计量：档位表的增益与偏置误差、时钟 ppm、通道时延、个体偏差与校准常数（衡量 5） | 核心 | G1 |
| G4 | 噪声随档位变化，抽取与处理在码值上做，峰值检测与高分辨率（衡量 6；修改 D46） | 核心 | G1 |
| G5 | 触发路径：独立检测、亚采样插值、NORMal 等待推进时间（衡量 7） | 核心 | G1 |
| G6 | `scope_measurements` 模块（含 #131 的算法、统计与门控）；`waveform_transfer` 的声明式二进制头、浮点块与点数模式；SDS2104X Plus 改为通用模块，删除两个型号专用组件（衡量 1） | 核心 | G1、#131 |
| G7 | 低频校准参考示波器改用独立模块 | 可认领 | G1 |
| T1 | 示波器模板（第 7 节），生成上述模块的组合 | 核心 | G6 |

G2–G5 在 G1 之后可以同时进行。万用表、函数发生器（`siglent_sdg` 也是型号专用）、校准源、SMU、频谱仪随后按同样的步骤做：先写本节的一个小节（现状、保真度缺陷、拆分、衡量），再拆分，再做模板。
