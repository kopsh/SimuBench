# 由用户与 Agent 搭建仪表（设计稿）

状态：设计稿，待按第 6 节的 issue 逐项实现。对应 D3 第 3 层“搭建仪表”，以及 architecture.md 3.3–3.5、7.5 中已规划但尚未实现的部分。

## 0. 目标与衡量

SimuBench 不可能为用户提供所有型号的仪表。平台要提供的是**搭建仪表的能力**：工程师或 Agent（经 MCP）根据编程手册、或既有测试程序的接口契约，自己写出仪表定义，不需要修改 SimuBench 的源码。

**衡量标准**：不改平台代码，只用 YAML 定义（第二遍：只通过 MCP 工具）重建低频校准工作台（[lowfreq-cal.md](lowfreq-cal.md)），并通过同一组验收测试（`tests/test_lowfreq_cal.py`、`acceptance/test_lowfreq_cal_scenario.py`）。

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

### 2.1 方言

定义新增 `dialect`，说明这台仪表的应答怎么写，默认值即现在的 SCPI 行为：

```yaml
dialect:
  echo_header: $comm.header      # off | short | long，或绑定到一个状态量（COMM_HEADER 可切换）
  number: engineering            # 现有 nr1/nr2/nr3 之外新增 engineering：200E-3、10E+3
  enum: short
  multipliers: {K: 1.0e3, M: 1.0e6, MA: 1.0e6, G: 1.0e9}   # 无单位数值的倍率后缀（点数）
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
```

只支持 `map`、`scale`、`offset`、`default`，不引入表达式语言：表达式难以校验、难以让 Agent 写对，复杂换算走插件。

### 2.4 公共命令与未知命令

已实现（低频校准工作台）：定义可以给公共命令自己的行为（`*CLS?`），`unknown_commands: ignore`（D42）。保持不变。

## 3. 通用波形传输与采集（G2、G3）

### 3.1 `waveform_transfer`

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

### 5.2 组件元数据（G7）

组件类型声明它在电路中的角色，测试台校验据此判断，不再写死组件名：`role: driver`（驱动源）、`role: series`（串联、阻抗可忽略，端接判断穿过它）、`impedance_of(params)`（已为输入端口实现）。内置组件补齐声明；插件组件同样可以声明。

### 5.3 组件目录（G5）

`GET /api/components` 与 MCP `list_components`：全部组件类型（含内部组件），每个给出端口、参数（默认值、单位、说明）与 op（名称、参数、说明），由类的声明与文档字符串生成。`get_definition` 补充组件与连线。

### 5.4 MCP 编写工具（G5）

| 工具 | 内容 |
| --- | --- |
| `list_components` | 5.3 |
| `create_definition` / `edit_definition` | 写入工作区 `instruments/`；编辑以 JSON Patch 表达，带读取时的哈希（乐观并发，D25）；每次保存进入版本历史（D35），标为 Agent |
| `validate_definition` | 5.1 |
| `draft_definition_from_script` | 在 `draft_bench_from_script` 的静态分析之上，为模型不支持的命令生成命令与状态量草稿（类型按参数推测，标为待确认） |
| `run_cases` | 5.5，返回每条用例的结果 |

工具共享现有的限流与 `guard`；不写代码文件。

### 5.5 用例运行命令（G8）

`simubench cases <cases.yaml> [--junit 报告]`：在刚上电的测试台上逐条运行用例（与 `tests/test_real_models.py` 同一个运行器，移入 `library/coverage.py`），退出码与 JUnit 报告供 CI 使用（D6）。真实型号的手册用例与按契约编写的契约用例用同一种文件。

## 6. 分期

| issue | 内容 | 依赖 |
| --- | --- | --- |
| 1 | 定义的静态校验（5.1）与组件角色元数据（5.2） | — |
| 2 | 组件目录接口与 MCP `list_components`（5.3） | — |
| 3 | 声明式应答：方言、应答模板、`pairs` 参数、倍率后缀、参数映射（第 2 节） | 1 |
| 4 | 通用波形传输组件（3.1） | 1 |
| 5 | 可配置的采集组件，Siglent 与 LeCroy 改为配置（3.2） | 4 |
| 6 | `simubench cases` 与 MCP `run_cases`（5.5） | — |
| 7 | MCP 编写工具：创建、编辑、校验、由脚本生成定义草稿（5.4） | 1、2、6 |
| 8 | 插件出口：入口点与工作区插件（第 4 节） | 1 |
| 9 | 验收：只用 YAML、再只用 MCP 重建低频校准工作台（第 0 节） | 3、4、5、7 |

界面中的定义编辑（D3 第 3 层的编辑）不在本期：定义文件仍可用文本编辑器与 MCP 修改，界面的“查看内部结构”（issue 20）显示结果。
