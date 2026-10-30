# 由用户与 Agent 搭建仪表（设计稿）

状态：第 1–6 节已实现（总 issue #39）；第 7 节为第二期的设计稿（总 issue #123）；第 8 节为组件粒度的设计稿，从示波器开始。对应 D3 第 3 层“搭建仪表”，以及 architecture.md 3.3–3.5、7.5 中已规划但尚未实现的部分。

## 0. 目标与衡量

SimuBench 不可能为用户提供所有型号的仪表。平台要提供的是**搭建仪表的能力**：工程师或 Agent（经 MCP）根据编程手册、或既有测试程序的接口契约，自己写出仪表定义，不需要修改 SimuBench 的源码。

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
  multipliers: {K: 1.0e3, M: 1.0e6, MA: 1.0e6, G: 1.0e9}   # 无单位数值的倍率后缀（点数）
format:
  number: engineering            # 现有 nr1/nr2/nr3 之外新增 engineering：200E-3、10E+3
  boolean: word
  enum: short
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

### 2.4 公共命令与未知命令

已实现（低频校准工作台）：定义可以给公共命令自己的行为（`*CLS?`），`unknown_commands: ignore`（D42）。保持不变。

### 2.5 #48 补充的声明式字段与通用组件

把参考示波器最后的专用行为改为声明时补充（都与型号无关）：

- **按参数区分的命令** `when`：几条命令共用一个命令头，按参数的取值选择（`C1:INSPECT? "DATA_ARRAY_1"` 回读数据，`"WAVEDESC"` 给出描述）；都不匹配时回复 -224。静态校验只在 `when` 能区分时才不报 `E-DEF-HEADER-DUPLICATE`。
- **索引参数** `index: true`：多状态命令的一个整数参数选择其他参数写哪组设置，状态路径中写作 `{名称}`：`PACU 1,AMPL,C1` 写 `measure.p1.kind` 与 `measure.p1.source`；查询也带它（`PACU? 1` → `PACU 1,AMPL,C1`），没有设置的可选值不写出（`PACU 1,NULL`）。静态校验按索引的取值范围检查每个状态。枚举的默认值可以是 `""`（尚未设置）。
- **操作应答的字段**：操作可以在结果中给出命名字段（`@op(fields=...)`），命令的 `reply` 模板引用它们：`'{header} {kind},{value} {unit},{status}'`（`C1:PAVA AMPL,200.1E-3 V,OK`）。组件目录与静态校验都认得这些字段。
- **通用组件与操作**：`system`（`clear_status`：`*CLS` 及其查询的回复；`reset`：与 `*RST` 相同的预置）；`scope_acquisition.measurement`（按 `measurements` 表的命名测量，值不能测量时为 `invalid_value` 与 `status` 的第二项）；`waveform_transfer.describe`（按 `descriptor` 的格式写出记录的点数、首点、抽点、采样间隔、偏移、增益、时基与采集次数）。输入阻抗、前端耦合用 `input_port` 与 `scope_frontend` 加参数映射表达。
- **测试台编辑** `set_identity`：设置实例的 `idn`、`serial`、`firmware`（MCP 原来无法写出 `*IDN?` 的型号串）。

## 3. 通用波形传输与采集（G2、G3）

### 3.1 `waveform_transfer`

已实现（#43，`library/components/transfer.py`）：sb 示波器、Siglent SDS 与低频校准参考示波器的数据回读都改为本组件的配置，回复与改动前逐字节一致。与下面的设计稿的差别：文本的前后缀不放在组件里，由命令的 `reply` 模板加上（`'{header} " {value} "'`，操作命令的应答也走模板，随 `COMM_HEADER` 回显或省略命令头）；ADC 码由参数描述：`codes_per_div`、`offset`（`subtract` 或 `add` 记录的偏移设置）、`adc_bits`（限幅）、`width`（8 或 16 位，按位移对齐）、`unsigned`、`byteorder`，都可以绑定或映射设置（例如 sb 的 `:WAVeform:FORMat` 同时决定编码、码值比例与字宽）；`block_digits`（如 Siglent 前导的 `#9`）、`trailer`（块后的换行）。“当前记录是哪一个”（读取开始时是否重新采集）由采集组件的 `transfer_record` 回答，留给 #44。

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

把 `ScopeAcquisition`、`SiglentSdsAcquisition`、`LecroyAcquisition` 合成一个可配置的 `scope_acquisition`：

已实现（#44，`library/components/scope.py`）：三台示波器（sb、Siglent SDS、低频校准参考示波器）的记录长度、触发、运行控制、噪声与 ADC 都写在定义里，回复、虚拟时间与采集事件与改动前一致（只有 Siglent 平均采集的耗时按 D46 改为 N 次）。`siglent_sds_acquisition` 与 `lecroy_acquisition` 只剩专用命令（WAVEDESC、`:TRIGger:STATus?`、依赖其他设置的范围、`INSPECT?`、`PAVA?`）。与下面的设计稿的差别：参数是平铺的（与 `waveform_transfer` 一致，每项都可以绑定或映射设置）：

- 记录长度：`record_length`（`fixed`：`points` 点覆盖 10 格；`exact`；`rate_ladder`）、`points`、`max_rate`，同一 ADC 的通道对 `rate_pairs` 同开时为 `max_rate_paired`；`delay` 绑定时触发点在中心之前 `delay` 秒；
- 触发：`trigger_source`、`trigger_level`、`trigger_slope`，按通道的设置写作 `$ch{n}.trigger_level`（`{n}` 是触发通道号，静态校验同样检查），`rising`、`falling` 列出两个沿的取值；非通道的触发源（外触发、电源）不触发；
- 运行控制：`mode` 与 `auto`、`normal`、`single`、`stop`、`force`（强制一帧后停止，Siglent 的 FTRIG）各自的取值，`after_single`（为空时模式不变、停止运行），`reacquire`，`missing`（`acquire`：没有记录的通道读取时按当前信号采一次，`error`：报 -230），`untriggered`（未触发的采集耗时：整个触发搜索或一帧）。等待中的单次采集在每次读取时重试。通用的 op：`run_mode`、`single`、`run`、`stop`、`force_trigger`、`wait`、`opc`、`digitize`；
- ADC：`adc_bits`（0：不量化）、`adc_codes_per_div`（0：`2**bits / adc_divs`）、`adc_offset`；
- 噪声：`noise_rms`、`noise_key`（`time` 时 `noise_stream` 绑定的设置选择另一组噪声，如采集方式）、`trigger_jitter_s`，`averaging` 与 `averages`。

原设计稿：

```yaml
- name: acq
  type: scope_acquisition
  params:
    record_length: {points: $memory.size, rule: exact}     # exact | rate_ladder（按 1-2-5 取采样率）
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
| `E-DEF-OP-UNKNOWN` | 组件或操作不存在；通道组件的操作用在没有 `<ch>` 的命令头上 |
| `E-DEF-WIRE-COMPONENT`、`E-DEF-WIRE-PORT` | 内部连线、端口映射指向不存在的组件或端口；由连线创建的端口（`in1..inN`）由组件的 `port_pattern` 声明 |
| `E-DEF-REPLY`、`E-DEF-DIALECT`、`W-DEF-MAP-KEY` | 应答模板引用不存在的状态（或没有 `state` 的命令用 `{value}`）；`echo_header` 绑定的不是 OFF/SHORT/LONG 枚举；参数映射的表中有设置取不到的值（#42） |
| `E-DEF-DEVIATION`、`W-DEF-DEVIATION-ZERO` | 个体偏差指向不存在的组件或参数、非数值参数；按 % 或 ppm 的相对偏差用在标称值为 0 的参数上（不起作用，#34） |
| `E-DEF-HEADER`、`E-DEF-HEADER-DUPLICATE` | 命令头无法解析（例如 `[1]` 不是节点）；后写的命令被先写的命令匹配了某种写法，永远不会执行 |

### 5.2 组件元数据（G7）

组件类型声明它在电路中的角色，测试台校验据此判断，不再写死组件名：`role: driver`（驱动源）、`role: series`（串联、阻抗可忽略，端接判断穿过它）、`impedance_of(params)`（已为输入端口实现）。内置组件补齐声明；插件组件同样可以声明。

已实现（#40）：组件类上的 `role`；驱动源是 `role: driver` 组件的电气输出端口（`out` 或 `inout`）。内置声明：`output_stage`、`unity_buffer`、`dc_supply` 为 driver，`coax_cable` 为 series；`smu_core` 不是 driver（到限值时吸收电流，与电源接在同一网络是合法的第二象限测试台）。

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

工具共享现有的限流与 `guard`；不写代码文件。

已实现（#46，`server/definitionfiles.py`、`engine/jsonpatch.py`、`engine/scriptscan.py` 的 `draft_definition`）：

- `read_definition` 返回文件内容、`sha256` 与诊断；工作区之外的定义（内置、搜索路径上的）`editable: false`，要修改就以新 id（或同 id，工作区的文件优先）`create_definition` 一份副本。
- `create_definition` / `edit_definition` 写 `instruments/<id>.yaml`；没有通过校验的内容也保存（与测试台相同，进行中的工作不丢），诊断随结果返回。`edit_definition` 的补丁整体应用或整体不应用；`sha256` 过期时报冲突并提示先 `read_definition`。每次保存进入版本历史（`.simubench/history/instruments/<id>.yaml/`，来源 `agent`）。
- JSON 中没有无穷大：定义里的 `.inf` 在 MCP 中写作 `"INF"`（与测试台文件相同），保存后照常加载。
- `draft_definition_from_script`：对脚本中的一台仪器（多台时用 `receiver` 指定），在 `base_definition`（不给时为空骨架）之上，为它不支持的每个命令头生成状态量与命令：数值（带 V、HZ、S 等单位）、ON/OFF、单词（枚举）、带引号（字符串）；多个参数的命令生成多个状态量与绑定参数的命令；只查询的命令为只读字符串。起草的条目在 `label` 与 `doc` 中标为“草稿（待确认）”，并列在返回的 `drafted` 中。f-string 的占位符按 `1` 读取，所以循环里的通道号只看得到 1，通道数需要 Agent 自己改。
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
| W4 | Agent 搭建要从组件写起：万用表要懂 `dmm_core`、`trigger_model`、`calibration_store` 的参数与绑定，34465A 的定义约 500 行，出错多、轮次多 | `library/instruments/keysight-34465a.yaml` |
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

### 7.3 Web：真机式面板（W3）

用户配置仪表用面板，与操作真实仪表一样：

- 面板由定义生成。定义的 `display` 增加面板布局的声明：分组（例如“测量”“量程”“触发”）、每个设置用什么控件（按键组、旋钮式数值、开关、下拉）、主显示区显示哪些读数与单位、软键。Agent 经 MCP 写这部分，与写命令一样；
- 没有声明面板时，按类别给出默认布局：万用表为大号读数加功能键、量程键；校准源为输出值、工作 / 待机键。都没有时退回现在的通用布局；
- 已有专用屏幕的类别（示波器、函数发生器、锁相放大器、频谱仪、SMU）保持不变；
- 搭建模式下，面板设置的是测试台文件中的初始设置；操作模式下是运行中的设置（#103 的说明照常显示）。脚本连着时遵守远程状态（#121）。

面板只改设置，不改仪表定义。

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
| `try_definition` | 在只含这台仪表的临时会话上依次发送命令，返回每条的回复、新增的错误与虚拟时间；不需要测试台，不写运行记录 |

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
| 万用表 | `dmm_core`（输入阻抗、检波、量程、误差、量化、校准修正）+ `trigger_model`、`reading_buffer`、`calibration_store` | 测量链是一整块，只有电压功能 |
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

- **水平**：时基、记录长度规则（fixed / exact / rate_ladder）、采样率上限与通道配对；
- **触发**：边沿触发的源、电平、斜率，以及触发抖动；
- **垂直**：每个通道的刻度、偏置、探头比，从状态量直接读取；
- **ADC 与噪声**：位数、每格码值、偏置的加减；输入端白噪声；
- **运行控制**：AUTO / NORMAL / SINGLE / STOP / FORCE 的状态机，何时重新采集，平均；
- **自动测量**：命名的测量；
- **命令操作**：`digitize`、`preamble`、`*OPC`、`wait`。

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
| F6 | 触发在记录上按采样点搜索，不做亚采样插值；NORMal 等待不推进虚拟时间；`*RST`、改时基没有声明耗时；频响为一阶低通；削波后的测量仍当有效值 | `scope.py` `_trigger_search`、`ScopeFrontend` |

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
| `scope_trigger`（共享） | **独立的触发路径**：在 ADC 之前的信号上检测，带自己的噪声；耦合（AC、低频抑制、高频抑制）、迟滞与噪声抑制、释抑；**亚采样插值**，给出触发时刻；外触发与电源触发作为端口或源。第一期只做边沿，脉宽等触发类型等有了事件型信号源（D14）再做。状态文字与状态位按声明映射 | F6 |
| `scope_acquisition`（共享，缩小） | **运行控制与同步**：运行状态机改为声明式状态表；采集作为待完成操作（`register_pending`），状态随虚拟时间依次经过等待、已触发、停止；同步语义按型号声明——`*OPC?` 是否等待采集、等待中读取得到上一帧还是报错、没有触发时 `:DIGitize` 阻塞还是返回。**抽取与处理**：普通、峰值检测、平均、高分辨率，都在码值上做（先加噪声，再量化，最后处理）。死区时间、记录保存、`digitize`、只读的采集次数 | F1、F4 |
| `scope_measurements`（共享） | 参考电平（10 / 50 / 90 % 或绝对值）与迟滞、顶值与底值的求法（直方图或极值）、最少周期数、削波判定、门控、统计（次数、均值、σ、最小、最大）、无效值规则 | F2、F6 |
| `waveform_transfer`（共享） | 编码（不变）；**声明式的二进制头**（字段名、偏移、类型、字节序、取值来源），WAVEDESC 由此写成定义；浮点块（float32 / float64）；点数模式与按请求点数抽取；每次读取的上限与超限行为；亚采样的 xorigin | 取代 `siglent_sds_acquisition` 的传输部分 |
| 状态与事件寄存器 | 平台的 `system` 组件读取采集与触发模块的状态位（等待触发、运行、采集完成），型号声明位号 | 仪表开发者需要 |

模块之间通过参数中的组件名引用，与 `trigger_model` 引用 `sampler` 相同。

**宏**：`scope_acquisition` 照旧接受现在的全部参数。没有引用独立模块时，加载定义时按这些参数展开为上述模块；展开后与显式写出的定义指纹相同。

**暂不做**：数学通道与 FFT（放在测量之后，作为测量与传输的虚拟数据源）、数字通道、协议解码、参考波形、模板测试、分段存储（留接口）。

**能否覆盖其他厂商**：按上表，Keysight 的 `:DIGitize` 阻塞与点数模式、R&S 的 `SING;*OPC?` 与 `FORM REAL,32`、Tek 的 `ACQ:STATE` 与 `WFMOutpre?` 的换算字段、Rigol 的分批读取上限与 `:TRIG:STAT?` 的取值，都可以用声明表达。这些厂商行为来自经验，未在仓库中核实，写进型号定义前要对照手册。

### 8.5 示波器：衡量

**结构**：

1. `siglent_sds_acquisition` 与 `siglent_sds_channel_limits` 删除，SDS2104X Plus 只用通用模块写成；#93 的例外清单去掉这两项；
2. `sb-dso-4`、`sb-dso-2x` 与低频校准参考示波器改用独立模块；不改的副本经宏展开，指纹与显式写出的相同；
3. 内部结构图（#20）显示新模块与连线；`list_components` 中每个新模块的参数都有说明。

**保真度**（在 SDS2104X Plus 上，各有用例或测试）：

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
