# 由用户与 Agent 搭建仪表（设计稿）

状态：设计稿，按第 6 节的 issue 逐项实现（总 issue #39）。对应 D3 第 3 层“搭建仪表”，以及 architecture.md 3.3–3.5、7.5 中已规划但尚未实现的部分。

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

界面中的定义编辑（D3 第 3 层的编辑）不在本期：定义文件仍可用文本编辑器与 MCP 修改，界面的“查看内部结构”（issue 20）显示结果。
