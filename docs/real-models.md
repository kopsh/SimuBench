# 真实型号仪表（D42）

目的：用真实型号演示 SimuBench。为某个型号写的脚本（SCPI 写法、`*IDN?` 检查、返回格式解析、错误处理）不改一行即可在虚拟仪表上运行。行为以该型号的编程手册为准，物理量仍由电路求解得到（D13）。

本文是所有型号共同遵守的规则。每个型号一个 issue，按本文实现。

## 1. 命名与身份

- 定义文件：`src/simubench/library/instruments/<厂商>-<型号>.yaml`，id 全小写，如 `keysight-34465a`、`fluke-5522a`。用户自建的定义放在工作区的 `instruments/`（见第 9 节）。
- `manufacturer`、`model` 与 `identity.idn` 按依据中 `*IDN?` 的返回格式填写，如 `Keysight Technologies,34465A,<序列号>,<固件版本>`。序列号与固件版本的格式也与真机一致；用户自建的仪表由作者定义 `*IDN?` 的写法。
- 依据字段 `reference`：作者选定、提供并维护的依据（厂商手册、自己的设计规格或接口契约），记名称、文档编号、版本或日期、对应的固件/规格版本。同一型号不同固件或不同规格草案的命令可能不同，以此为准。带 `reference` 的定义不论内置或工作区，都按真实型号的规则检查（第 6 节）。
- 内置仪表只有真实型号（D42）。`sb-*` 是 SimuBench 自造的验证仪表，定义在仓库的 `validation/instruments/`，不随软件包发布；测试与验收通过定义搜索路径加载（见第 9 节）。

## 2. 命令的三种实现程度

依据中的每条命令，属于以下之一，并记入覆盖表（第 6 节）：

| 程度 | 含义 | 例子 |
| --- | --- | --- |
| 实现 | 语法、参数范围、默认值、返回格式、错误与对测量的影响都按依据 | `:MEASure:VOLTage:DC?`、`:SENSe:VOLTage:DC:NPLC` |
| 接受 | 按依据解析、校验并保存，查询返回保存的值，但**不改变采集、测量、传输与回复的数值** | 显示、蜂鸣器、界面语言、只影响面板显示的单位 |
| 未实现 | 回复 `-113,"Undefined header;not implemented in this definition"`，并在事件历史中标注 | 尚未做的功能 |

未实现的命令必须报错，不能静默接受：脚本在虚拟环境里通过、上了真机却失败，比报错更糟。因此"接受"只用于与数据无关的命令，覆盖表中每条"接受"都必须写 `affects_data: false`（一致性检查对缺少它或写为 `true` 的条目报错）。会改变数据的命令只有两条路：按依据实现；或者标为"未实现"（一条命令只有一部分取值能实现时，把不能实现的取值从枚举/范围中去掉，使设置它们按依据报错，例如只支持 `ASCii` 的 `FORMat[:DATA]`）。"未实现"的回复是 `-113,"Undefined header;not implemented in this definition"`：说明的是**这个定义尚未实现**，不把责任归给 SimuBench（依据由作者提供，#172）。

## 3. 按依据对齐的细节

- **语法**：长短格式、可省略的节点、数字后缀（通道号）、单位与倍率后缀（`MV`、`KHZ`）、`MIN`/`MAX`/`DEF`、列表与字符串参数的写法。
- **默认值**：`default` 为上电值，`rst` 为 `*RST` 之后的值，与手册的默认值表逐行一致。
- **返回格式**：数字的位数、符号、指数写法（如 `+1.00000000E+00`）、布尔值、枚举的大小写与短格式、多个值的分隔符，都与手册一致。定义中可按命令声明返回格式。
- **错误**：错误码与错误信息文字与手册的错误列表一致，包括设备专用错误码；错误队列长度与溢出（`-350`）也按手册。定义中可声明错误表。
- **触发与时序**：触发系统（`INITiate`、`FETCh?`、`READ?`、`*TRG`、触发源与计数）按手册。测量和命令所需时间（积分时间、建立时间）按手册计入虚拟时间（D12）。
- **状态系统**：`*OPC`、`*WAI`、`*ESR?`、`*STB?`、`*SRE`、`*ESE` 及手册中的操作与可疑状态寄存器。
- **物理量**：测量值来自电路求解与组件模型，不按命令伪造。手册中的精度指标记入定义（可选的 `specs` 字段），参与计算的个体偏差范围写在 `deviations` 中（[metrology-authoring.md](metrology-authoring.md) 第 7 节）。

## 4. 手册用例

每个型号在 `tests/models/<id>/cases.yaml` 中写测试用例；工作区中的自建定义把 `cases.yaml` 放在定义旁边或工作区的任意位置（运行器按 `model` 找定义）。每条用例是一串命令与预期，`manual` 写依据文件中的章节或条目（厂商手册、设计规格或接口契约）：

```yaml
- name: "*RST 后的直流电压量程为自动"
  manual: "Command Reference > SENSe:VOLTage:DC:RANGe:AUTO"
  steps:
    - send: "*RST"
    - query: ":SENS:VOLT:DC:RANG:AUTO?"
      expect: "1"
- name: "超出范围的 NPLC"
  manual: "Command Reference > SENSe:VOLTage:DC:NPLC"
  steps:
    - send: ":SENS:VOLT:DC:NPLC 1000"
    - query: ":SYST:ERR?"
      expect_prefix: '-222,"Data out of range'
```

一个通用的测试读取所有用例文件，逐条运行（与 `simubench cases` 同一个运行器，`library/coverage.py`）。预期写它的含义，不写正则（D49、#926；`library/expectations.py`），每个查询步骤只给一个：

| 键 | 判断 |
|---|---|
| `expect` | 回复与它完全相同 |
| `expect_prefix` | 回复以它开头：错误码与消息的开头（`'-222,"Data out of range'`） |
| `expect_value` + `tolerance` | 回复是一个数，在期望值 ± 容差内 |
| `expect_values` + `tolerance` | 回复按逗号切分，逐个数比较（#836）；`tolerance` 可以是一个数，也可以每个值一个（各点在各自量程的 4σ 内） |
| `expect_fields` | 回复切成字段（默认按逗号；`{separator, prefix, suffix, fields}` 写别的分隔与首尾），每个字段：字符串 = 原样相同，数 = 期望值 ± 步骤的 `tolerance`，映射 = `value`/`tolerance`、`min`/`max`、`unit`（数后的单位 `2.05E+0 V`）、`prefix`/`suffix`（数前后的文字，`"VOLT +1.0E+01`）、`format`、`one_of`、`contains`、`length`、`nonempty`，`count: N` 表示连续 N 个同样的字段 |
| `expect_format` | 整个回复的写法 |
| `expect_block` | IEEE 488.2 块：`header`（`#800001000`）、`bytes`、`indefinite`（`#0` 块）、`starts`/`contains`、`fields`（块内是 ASCII 数时）、`end`（块后的结束符，默认 `""` 或 `"\n"`） |

**写法**（`format`、`expect_format`）只在手册规定了写法时才写：命名格式 `unsigned`（只有数字）、`integer`/`nr1`（可带符号）、`nr2`、`nr3`、`number`，或图样——`d` 一位数字，`±` 必须有的符号，`~` 可有可无的符号，其他字符照原样（`±d.ddddddddE±dd`、`dddd,dd,dd`）。`expect_value(s)` 加 `format` 时值与写法都要对。带噪读数写期望值 ± 容差，容差取型号的噪声 σ（`specs` 里的值，几倍 σ 写在注释里），不按显示位数写包络：位数随量级变化，包络会放过错误的量级、拦住正确的读数（#924、#926）。旧的 `expect_match`（正则）不再接受，载入时报错并指出该用哪一种。用例应覆盖覆盖表中每条“实现”和“接受”的命令；带 `reference` 的定义每条用例都要写 `manual`（覆盖检查报告缺少的）。

同一种文件也用来写**契约用例**：按既有测试程序的接口契约而不是厂商手册编写，`manual` 写契约条目（例如 `examples/lowfreq-cal/cases.yaml`）。步骤可以用 `instrument` 发给测试台中的另一台仪器。运行：

```bash
simubench cases examples/lowfreq-cal/cases.yaml --junit cases.xml   # 失败时给出步骤、命令、预期与实际回复，退出码 1
```

MCP 的 `run_cases` 运行工作区中的用例文件，返回每条用例的结果。

## 5. 演示用的测试台

每个型号至少带一个示例测试台（`examples/benches/`），让它和其他仪表、DUT 连在一起时能测出符合物理的结果。`examples/` 只用真实型号；用 `sb-*` 的测试台放在 `validation/benches/` 或 `acceptance/benches/`（CI 检查）。

## 6. 覆盖表

每个型号一个 `src/simubench/library/instruments/<id>.coverage.yaml`（工作区中的定义放在定义旁边），按作者所依据的文件列出全部命令及其程度（实现、接受、未实现）与说明。

- CI 检查覆盖表与定义一致：定义中的命令都在覆盖表中，标为“实现”或“接受”的命令都在定义中，并且都有用例；“接受”条目必须有 `affects_data: false`。带 `reference` 的工作区定义同样适用（`simubench coverage`、`simubench cases`、MCP `run_cases`）。
- **未实现的原因**（#1037）：未实现的条目可写 `gap`，说明为什么没有实现——`out_of_scope`（不在仿真范围内：[product-plan.md](product-plan.md) 第 6 节的应用层（抖动、眼图、PAM、均衡、协议解码、用户代码）、云与仓库服务、真实时钟、需要真实硬件的服务流程（校准、自检、探头校准））、`blocked`（等平台的某项能力，`blocked_by` 写能力：[product-plan.md](product-plan.md) 4.2 的编号如 `C5`，或 4.2 尚未列出的能力的短名如 `mask-test`）、`model_work`（平台已有能力，只差写进定义）；`note` 写具体内容。`gap` 与 `blocked_by` 只能写在未实现的条目上，`blocked` 必须有 `blocked_by`。没有 `gap` 的未实现条目算“未分拣”。SimuBench 是本地工具，用户脚本里的文件与设置命令要能用（#1073）：文件系统写 `blocked_by: file-system`（#1069），设置存取写 `setup-memory`（#1070），波形与结果存为文件写 `data-file`（#1071），截图、硬拷贝与打印写 `screenshot`（#1072）；只改仪表自身配置、不影响数据的命令（LAN/GPIB/USB 等接口配置、LXI 标识、蜂鸣器、显示与界面偏好、打印机选择）写 `model_work`，候选“接受”（`affects_data: false`）；应用层分析结果的保存随该分析仍算 `out_of_scope`。
- 用户看得见：脚本发出一条覆盖表写明原因的未实现命令时，`-113` 的错误事件带 `gap`、`blocked_by` 与 `note`，TUI 日志与 Web 事件历史显示出来；Web 的命令页与 `GET /api/definitions/{id}/coverage` 给出每条命令的程度与原因。
- `simubench coverage <id>` 打印覆盖表与统计（未实现另按原因分：不做 · 等能力 · 可做 · 未分拣），用于演示时说明做到了什么程度。
- `simubench coverage <id> --draft <命令清单.txt>` 由命令头清单（每行一个，`# 章节` 分节，只抄依据中的命令名）生成覆盖表骨架，或给已有的覆盖表补上缺少的命令；写型号的步骤见 `.claude/skills/real-model/SKILL.md`。
- 内置的真实型号只使用通用组件（`generic`），例外清单在 `tests/test_model_components.py`，只能缩短（[metrology-authoring.md](metrology-authoring.md) 第 9 节）。
- **频谱仪的“求值方式”**（[signal-chain.md](signal-chain.md) 5.4，#961）：迹线数据与标记读数的命令（`:TRACe[:DATA]`、`:CALCulate:MARKer:Y` 一类）在说明中写明定义的 `specan_trace.evaluation`，以及它包含的前端非理想——
  - `analytic`（解析求值，默认，全部内置真实型号）：平坦度、失真产物（二阶/三阶截点）、增益压缩、镜像与杂散、残余与本振馈通、**相位噪声**都在迹线里，写出定义实际声明了哪几项及其数据手册出处，没有声明的写“未建模”与原因；
  - `sampled`（采样式求值）：平坦度、镜像、杂散与残余在（它们已在模拟谱线里），**相位噪声与失真产物、压缩没有**，说明中必须写明这一点；另写超出 `MAX_REQUEST_FANOUT` 的设置报错（“超出模型能力”）。
  例句（N9320B 的 `:TRACe[:DATA]`）：“Analytic evaluation (specan_trace evaluation: analytic): the trace includes the front end of the data sheet (…): noise figure from the typical DANL and second/third-order intercepts and 1 dB compression, each for the band holding the center frequency; SSB phase noise …. Not modeled: …”。

## 7. 版权与商标

- 只实现手册描述的行为。所依据的厂商手册存放在仓库的 `manuals/`（PDF 走 Git LFS，附文本版，索引见 `manuals/README.md`），仅供开发时查阅，不随 wheel 发布；定义、文档与界面不大段摘抄手册文字，命令名称和简短的语法说明除外。
- `*IDN?` 返回真实的厂商与型号，脚本才能不改一行地运行（D5）；发布版也如此（D42）。
- 只以名称指明所模拟的型号：界面与文档不使用厂商标志、配色等商业外观，不暗示厂商认可。
- 声明：SimuBench 与所模拟型号的厂商没有关联，也未获其认可；文中的厂商名与型号是各自所有者的商标，仅用于指明所模拟的仪表。

## 8. 字段说明（框架已实现）

以下字段都是可选的，`sb-*` 定义不用改。带 `reference` 的定义即为真实型号（内置或工作区自建）；内置定义都必须带 `reference`（`tests/test_release.py`）。

| 字段 | 位置 | 说明 |
| --- | --- | --- |
| `reference` | 定义 | 所依据的文件，由作者提供和管理：厂商手册、设计规格或接口契约。`title`（名称）、`document`（文档编号）、`version`（版本或日期）、`firmware`（对应的固件/规格版本）。有此字段时：未定义的命令回复 `-113,"Undefined header;not implemented in this definition"`，事件历史中该错误带 `reason: not_modeled`；错误信息只用依据文件里的文字，不附带 SimuBench 的诊断细节（细节仍记入事件历史）。 |
| `errors` | 定义 | 错误表 `{错误码: 信息文字}`。覆盖标准文字，也可新增设备专用错误码（如 `501`）；`0` 的文字用于“无错误”。 |
| `error_queue` | 定义 | 错误队列长度（默认 20）。队列满时最后一格变为 `-350`（SCPI-99 21.8），其文字也可在 `errors` 中给出。 |
| `format` | 定义 | 默认返回格式：`number`（`nr3` / `nr2` / `nr1` / `engineering`，后者即 `200E-3` / `si` / `general`，后者即 C 的 `%g`：`digits` 位有效数字、小写 e、去尾零，如 SR86x 的 `2.3346669042e-09`，#383）、`digits`（小数位数；`general` 为有效位数）、`sign`（非负数是否带 `+`）、`exponent_digits`、`integer`（`plain` / `signed` / `engineering`）、`boolean`（`numeric` 即 1/0，`word` 即 ON/OFF）、`enum`（`short` / `long`）、`error_code`（`:SYSTem:ERRor?` 中的错误码是否带 `+`，如 Keysight 的 `+0,"No error"`）。默认值即 `sb-*` 一直以来的格式（`+1.000000E+03`）。 |
| `commands[].format` | 命令 | 覆盖该命令的返回格式，字段同上。`enum` 还可以是 `as_defined`：按定义中的写法原样返回（如 Siglent 的 `RISing`）。 |
| `builtins` | 定义 | 这个型号回答的内置命令（如 `["*IDN", "*OPC", "*RST"]`）。不写时全部回答；写了以后，其余内置命令（`*CLS`、`:SYSTem:ERRor?` 等）按“未建模”处理（D45）。`*TRG` 也是内置命令：调用定义中带 `bus_trigger` 操作的组件（如 `trigger_model`），没有时按“未建模”回复。 |
| `display.actions` | 定义 | 面板按钮（`run`、`stop`、`single`）等价的型号命令，如 `{single: ":TRIGger:MODE SINGle"}`，在仪表内部执行、不经过 SCPI（D32）；不写时用采集组件的 `run`、`stop`、`single`（没有单次模式的型号为 `digitize`）。 |
| `specs` | 定义 | 手册中的精度指标，任意结构；只记录，不参与计算。参与计算的个体偏差范围写在 `deviations` 中（#34，[metrology-authoring.md](metrology-authoring.md) 第 7 节）。 |
| `deviations` | 定义 | 个体偏差的范围：`param`（`组件.参数`，通道组件每通道一个值）、`limit`、`unit`（`%`/`ppm` 为相对标称值；不写为参数本身的单位，值 = 标称 + 偏差）、`distribution`（`uniform`，或 `normal` 与 `coverage`，截断在 ±limit）、`by`（按表抽取），或对频率曲线参数写 `bands`（按频段，`limit` 每段一个，可选 `reference`、`points`；段间直线过渡、表外为 0，#1004、#1010）。同一参数在不同设置下指标不同时，每组设置一条，写 `when`（`{设置: 值}`，D70）与可选的 `option`（只在装了该选件的整机上抽，#1020）。测试台实例写 `deviation: from_specs` 时由种子抽取（[metrology-authoring.md](metrology-authoring.md) 第 7 节） |
| `options` | 定义 | 硬件选件（D68，#1004）：`components` 与 `commands` 描述不带任何选件的整机；每个选件写 `id`（厂商的写法，如 `P03`）、`label` 与 `params`（`{组件: {参数: 值}}`，装了这个选件的整机用它替换定义里的同名参数，可以是 `$设置` 绑定或映射）。命令写 `option: P03` 表示它属于该选件：没装的整机跳过这条命令，由同一命令头上后面一条不带 `option` 的命令回答（手册写明的“未装选件时的回复”），没有这样的命令时回 `-241`。只写手册列出且改变已建模行为的选件。 |
| `constraints` | 定义 | 手册写明不能同时取的设置值，或改一个设置时仪表自动改另一个（D69，#1013）：`when` / `forbid` / `action`（`refuse` 报 `error`，默认 -221；`coerce` 把被禁止的设置改成 `coerce` 的值）/ `source`。按改动后的状态判断，与先设哪个无关；写法见 `instrument-authoring.md` 2.12。手册没写错误码或另一个方向的做法时，覆盖表注明假设 |
| `options` | 测试台实例 | 这台仪表装了哪些选件，如 `options: [P03]`；定义里没有的选件报 `E-OPTION-UNKNOWN`。不写即不带选件。编辑操作 `set_options`（`{op: set_options, instrument: sa, options: [P03]}`，整体替换）改它，Web 检查器的“选件”开关与 MCP 的 `edit_bench` 都走这一操作（#1007）。 |
| `identity.options_reply` | 定义 | 内置 `*OPT?` 按实例已装的选件回复（D68，#1007）：回复是这段文字，其中 `{options}` 换成已装选件（按定义的顺序，`identity.options_separator` 分隔，缺省逗号）；没装任何选件时回 `identity.options_none`（缺省 `0`，IEEE 488.2 10.20）。不写时 `*OPT?` 照旧由定义自己的命令回答（如 M8194A 绑定状态量）。只在手册写明 `*OPT?` 的回复格式时使用。 |
| `unknown_commands` | 定义 | `error`（默认）：未定义的命令回复 `-113`；`ignore`：不报错、不回复，事件历史记为 `ignored`。只用于按接口契约编写、没有 `reference` 的定义（D42），真实型号不允许。 |
| `commands[].header` 为 `*XXX` | 命令 | 定义可以给公共命令自己的行为（如方言中的 `*CLS?`），优先于内置的公共命令。 |
| `idn` | 测试台实例 | 覆盖定义的 `*IDN?` 回复，字段同 `identity.idn`；用于主机程序校验的型号串。 |
| `dialect` | 定义 | 非 SCPI 方言的应答框架：`echo_header`（`off` / `short` / `long`，或绑定一个取值为 OFF、SHORT、LONG 的状态，如 LeCroy 的 `COMM_HEADER`，或一个布尔开关，如 Infiniium 的 `:SYSTem:HEADer`），`long_form`（布尔开关，开时命令头与枚举用长格式，如 `:SYSTem:LONGform`，#602），`multipliers`（无单位数值的倍率后缀，如 `MSIZ 10K`）。默认即 SCPI。见 [instrument-authoring.md](instrument-authoring.md) 2。 |
| `commands[].reply` | 命令 | 查询应答模板：`{header}`（按 `echo_header` 回显的命令头）、`{value}`（绑定的状态，或操作的应答）、`{value_block}`（同一应答包成 IEEE 488.2 定长块，如 `#13(@)`，#415）、`{<状态路径>}`，如 `"{header} {value} V"`。 |
| `commands[].params[].state`、`type: pairs` | 命令 | 参数各自写入一个状态（多状态命令，如 `TRSE EDGE,SR,C1`）；`pairs` 是 `名,值,名,值…`，`keys` 给出每个名对应的状态（`null` 为接受并忽略）。全部校验通过才写入。 |
| `state.*.ratio` | 状态 | 比例量（0.25）；写入大于 1 的值按百分数读（40 → 0.4）。 |
| 组件参数 `{from, map, scale, offset, default}` | 定义 | 由设置派生组件参数：查表（不在表中用 `default`，没有 `default` 则原值），再做 `值 × scale + offset`。 |

**覆盖表** `<id>.coverage.yaml`（与定义放在一起；仪表库不会把它当作定义）：

```yaml
schema: simubench.coverage/1
model: keysight-34465a
sections:
  - section: "Command Reference > SENSe Subsystem"
    commands:
      - {header: "[:SENSe]:VOLTage[:DC]:NPLC", level: implemented}
      - {header: "[:SENSe]:CURRent[:DC]:NPLC", level: not_modeled, note: 之后实现}
      - {header: ":DISPlay[:STATe]", level: accepted, affects_data: false}
```

`header` 与定义中的写法一致（比较时忽略大小写、空格和开头的冒号）。`*IDN`、`*RST`、`:SYSTem:ERRor[:NEXT]` 等内置命令不在定义里，也可以列为“实现”。CI（`tests/test_real_models.py`）检查：定义中的每条命令都在覆盖表中且不是“未实现”；标为“实现”或“接受”的命令都在定义中（或是内置命令），并且至少有一条手册用例使用它。不一致时，失败信息写出是哪一条命令。

**手册用例** `tests/models/<id>/cases.yaml`：第 4 节的列表写法即可；也可以写成映射，指定测试台：

```yaml
model: keysight-33522b          # 默认取文件夹名
bench: ../../../examples/benches/keysight_33522b_rc.bench.yaml   # 默认：只有这台仪表
instrument: awg                  # 默认 dut
cases:
  - name: ...
    manual: ...
    steps: [...]
```

回复是二进制块（如波形数据）的查询写 `binary: true`：按块头给出的长度读取，预期作用于按 Latin-1 解码、不去掉首尾空白的字节（包括块后的换行），通常写 `expect_block`。

每条用例在一个刚上电的测试台上运行，测试报告中单独列出，名称带手册章节。

**完整示例**：`tests/models/acme-dm1/` 是一个虚构的“真实型号”，只用于测试框架本身（不在产品仪表库中）：定义（`reference`、`errors`、`error_queue`、`format`、`specs`、命令级 `format`、没有冒号层级的 `OUT` 命令）、覆盖表与用例。`simubench coverage acme-dm1 --definitions tests/models/acme-dm1` 打印它的覆盖表与统计。

**第一个产品型号**：`siglent-sds2104xplus`（D45）。覆盖表由手册正文中的命令条目生成（622 条），实现与接受的级别和说明逐条手写；`examples/scripts/siglent_sds_read_waveform.py` 按手册的波形读取示例编写，在真机和 SimuBench 上都能直接运行（`tests/test_siglent_sds.py`）。

**第二个产品型号**：`siglent-sdg2042x`（D45）。SDG 的命令不是 SCPI 树，而是 Siglent 自己的键值写法（`C1:BSWV WVTP,SINE,FRQ,1000`），回复带命令头与单位（`C1:BSWV WVTP,SINE,FRQ,100HZ,...`），所以这些命令都是组件操作（`library/components/siglent_sdg.py`），由组件拼出完整回复。手册的命令头写法也不全是 SCPI 的长短格式：`ParaCoPy` 的短格式是 `PACP`，定义中写成 `PAraCoPy`，使大写字母就是短格式。组合消息里重复通道前缀的第二条单元（`C1:BSWV …;C1:OUTP ON`）按真机接受，第一条单元重新从该节点起，不按 SCPI 相对路径重复拼接成 `C1:C1:…`（issue 149；PG02_E05C 未单列组合命令一节，规则在 `scpi/parse.py` 的 `resolve_tokens`，引擎与静态分类共用）。

**第三个产品型号**：`keysight-dsox2024a`（D45，示波器的开发验证基准）。覆盖表由编程手册的命令索引生成（487 条），`*RST` 的示例回复逐条对过；第一批按手册补齐的命令（#160：`*TRG`、`:TRIGger:FORCe`、`:SYSTem:PRESet`、`:SERial?`、`:ACQuire?`、`:SYSTem:DIDentifier?`、前面板/菜单与缩放时基的“接受”命令，以及 `:ACQuire:TYPE` 的 PEAK 与 HRESolution 处理）逐条记在覆盖表中；状态寄存器、逐通道触发电平与更多自动测量所需的通用能力记在 #248；`examples/scripts/keysight_dsox_read_waveform.py` 按手册的波形章节编写（`:DIGitize`、`:WAVeform:PREamble?`、`(code - yreference) * yincrement + yorigin`），在真机和 SimuBench 上都能直接运行（`tests/test_keysight_dsox.py`）。为它补上的通用能力（`tests/test_number_formats.py`）：

- 返回格式 `significant`：有效数字位数固定的工程记数（`+40.0E+00`、`+60.0000000000000E-09`），由命令级 `format` 逐条给出；
- 命令的 `factor`：命令的数是设置乘以系数（`:CHANnel<n>:RANGe` 为 8 格，`:TIMebase:RANGe` 为 10 格），写入时除回去，范围仍按设置检查；
- 组件的 `on_state` 可以返回调整后的值：`scope_vertical` 的 `out_of_range: clamp` 把超出范围的档位与偏置设为最近的合法值（手册的写法），改档位时偏置随之收进新的范围；探头比改变时按 `rescales` 换算档位与偏置；
- `scope_timebase.reference_divs`：参考位置（左、中、右）决定触发前的格数；`reference_mode` / `reference_position`：型号用“参考方式 + 百分比”时（SDS 的 `:TIMebase:REFerence` / `:REFerence:POSition`），POSition 按记录点数的百分比放置触发，DELay 仍由延迟移动；
- `waveform_transfer`：`reserve_lowest`（最低码表示无数据，数据不落在它上面）、`ascii_block`（ASCII 列表放在定长块里）、头字段的 `when` / `else`、op `figure` 单独读出 x/y 增量、原点与参考；16 位字的 y 增量按移位后的码计算；
- `scope_trigger` 的 `reject` / `hf_reject`：触发路径上的低频、高频抑制滤波。

## 9. 内置定义与验证定义

- **内置**：`src/simubench/library/instruments/` 只放真实型号的定义与覆盖表，随 wheel 发布，是元件库、MCP `list_catalog` 与命令行默认列出的全部仪表。
- **验证**：`sb-*` 定义在 `validation/instruments/`，用到它们的测试台在 `validation/benches/`（开发时用的演示台）与 `acceptance/benches/`（验收场景）。只被它们使用的组件（锁相、频谱仪、SMU、电子负载、射频源、PRBS）留在引擎里。
- **搜索顺序**：测试台所在目录的 `instruments/`、工作区的 `instruments/`、环境变量 `SIMUBENCH_DEFINITIONS` 中的目录（多个目录用 `os.pathsep` 分隔，Linux 为 `:`，Windows 为 `;`），最后是内置定义。
- **加载验证定义**：`pytest` 由仓库根目录的 `conftest.py` 自动设置 `SIMUBENCH_DEFINITIONS`；开发时 `simubench serve --workspace validation`（`validation/` 本身就是一个带 `instruments/` 的工作区），或 `SIMUBENCH_DEFINITIONS=validation/instruments simubench serve ...`。
- **检查**：`tests/test_release.py` 检查内置定义都是真实型号、未设置搜索路径时仪表库中没有 `sb-*`、`examples/` 中没有 `sb-*` 引用；CI 的打包任务检查 wheel 中没有 `sb-*` 定义。
