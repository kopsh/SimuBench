# 计量类仪表的通用能力（设计稿）

状态：设计稿，按第 10 节的 issue 逐项实现（总 issue #86）。接续 [instrument-authoring.md](instrument-authoring.md)（#39）：那一期让示波器、信号源一类仪表只用 YAML 写出；这一期对直流与低频计量仪表（万用表、多功能校准源、源表）做同样的事，并开始 D3 第 2 层“技术指标 → 误差参数”。

## 0. 目标与衡量

下一批真实型号是 Keysight 34465A（#7）、Fluke 5522A（#8）、Keithley 2450（#12），以及用前两台做的定标演示（#9，D4）。平台为它们提供通用能力，型号本身只写定义、覆盖表、用例与测试台。

**衡量标准**：

1. 三个型号合并时，`src/simubench/library/components/` 中没有新增型号专用代码，由 CI 检查（第 9 节）；
2. 定标演示的真值核对用平台的通用命令完成（第 8 节），演示目录中没有核对代码。

本文示例中的数值都只是示意，型号的取值以其手册为准。

原则沿用 instrument-authoring.md 第 0 节：声明式优先；代码是出口；Agent 只写定义；先校验再运行。另加一条：

5. **按领域概念建组件，不按型号建**：量程、触发模型、校准系数、工作 / 待机是这类仪表共有的概念，组件按概念划分、参数可绑定设置；型号之间的差别全部落在参数与映射上。

## 1. 现状与缺口

| # | 缺口 | 证据 | issue |
| --- | --- | --- | --- |
| C1 | 测量核心没有量程：没有自动量程、超量程读数、按量程的误差与分辨率；输入阻抗写死在定义上 | `meters.py` `DmmCore` 只有 `DC`、`AC` 两个读数 | #87 |
| C2 | 触发系统与读数存储只在 `smu_core` 里；`*TRG` 不是内置命令；`*OPC?` / `*WAI` 不知道仪表还有没做完的操作 | `smu.py` 的 `initiate` / `fetch` / `buffer`；`instrument.py` `BUILTIN_HEADERS`；`scope.py` 自己覆盖 `opc` | #88、#94 |
| C3 | 校准只有一组系数，写在 `dmm_core` 的 op 里 | `meters.py` `cal_*` | #89 |
| C4 | 没有“工作 / 待机”的源、建立时间、过载跳回待机；参数不能按单位决定功能（`OUT 1 V, 1 kHz`）；`ERR?` 一类非 SCPI 的错误查询不能声明 | `ParamSpec.unit` 只有一个单位；`system` 只有 `clear_status`、`reset` | #90 |
| C5 | 源表的 LO 就是测试台地；没有 sense 端口；连线不能带电阻（配件要 `IN` / `OUT`，`resistor` 是 `A` / `B`） | `smu.py` `ports = {"hi": …}`；`session.py` 配件链 | #91 |
| C6 | 个体偏差只能由作者写死；`specs` 只保存，不参与计算 | `models/definition.py` `specs: dict[str, Any]` | #34 |
| C7 | 判定与真值的核对（`decide` / `truth` / `assess`）没有面向用户的入口 | `engine/calibration.py` 只有 `register_view` 被 CLI、API、MCP 使用 | #92 |
| C8 | “型号只写 YAML”没有检查；写型号的步骤散在各处 | `siglent_sdg.py`、`siglent_sds.py` 都是随型号 issue 加入的 | #93 |

## 2. 通用测量核心（C1，#87）

扩展 `dmm_core`，不改名，`sb-dmm-1` 的回复逐条不变。每项都是普通组件参数，可以绑定或映射设置（instrument-authoring.md 2.3）：

```yaml
- name: meter
  type: dmm_core
  params:
    function: {from: $sense.function, map: {"VOLT": DC, "VOLT:AC": AC}}
    ranges: {DC: [0.1, 1, 10, 100, 1000], AC: [0.1, 1, 10, 100, 750]}
    range: $sense.range            # 不在表中时取能容纳它的最小量程
    autorange: $sense.range_auto
    autorange_up: 1.2              # 满量程的比例：超过即升档
    autorange_down: 0.1            # 低于即降档
    overrange: 1.2                 # 超过即超量程
    overload_value: 9.9e37
    resolution: {from: $sense.nplc, table: {0.02: 3.0e-4, 0.2: 3.0e-5, 1: 3.0e-6, 10: 1.0e-6, 100: 3.0e-7}}
    z_in: 1.0e7
    z_in_high: 1.0e10
    high_z: $input.impedance_auto
    high_z_ranges: [0.1, 1, 10]
    gain_error_ppm: {DC: {0.1: 8, 1: 3, 10: 2, 100: 4, 1000: 4}, AC: 60}   # 隐藏：按功能、量程
```

- **量程选择**：手动量程按 SCPI 的规则（取能容纳设定值的最小量程）；自动量程按**真值**选档（滞回由 `autorange_up` / `down` 给出），选档本身不推进虚拟时间（换档时间以后可以加参数）。op `range` 返回实际使用的量程。
- **超量程**：`|读数| > overrange × 满量程` 时读数为 `overload_value`，由定义的 `format` 写出（34465A 为 `+9.90000000E+37`）。
- **分辨率**：读数按 `resolution × 满量程` 量化；不写时保持现在的 `digits`。参数映射新增 `table`：按设置的取值查表，取不大于该值的最近一项（NPLC 0.5 用 0.2 的值）。这是对 2.3 的唯一扩展，仍不引入表达式。
- **输入阻抗**：`high_z` 为真且当前量程在 `high_z_ranges` 中时为 `z_in_high`，否则为 `z_in`；它进入电路求解（测试台的分压电路读数随之变化）。实现（#87）：**测量核心自带输入级**——设了 `z_in` 的 `dmm_core` 把 `in` 端口当作仪表的电气端子，自己将对地电阻 stamp 进测试台电路，量程或 `high_z` 变化使阻抗不同时报会话重解；不设 `z_in` 时核心读连线进来的信号（`input_port` 在前面拥有电气侧，`sb-dmm-1` 原样保留在这条旧路上）。没有采用「`input_port` 绑定组件导出值」：那需要新增组件到组件的绑定机制，而阻抗的判定（功能、量程、`high_z`）本来就在核心手里。
- **按量程的误差**：隐藏的 `gain_error_ppm`、`offset` 可以是一个数（所有量程相同，现在的行为），也可以是按功能、量程的表。这是 #9 “有的量程超差、有的量程在保护带内”的前提，也是第 7 节按量程抽取偏差的落点。
- **`sampler` 协议**：核心提供 `sample(now) -> (读数, 耗时)` 给触发模型（第 3 节）；耗时 = NPLC × 电源周期 +（自动调零时）再一次积分 + 固定开销，与 `smu_core` 现在的读数时间规则相同，两者共用实现。

### 2.1 实现与设计的差别（#87）

- 量程、自动量程（滞回带 `[autorange_down, autorange_up] × 满量程`，出带即取能容纳真值的最小量程）、超量程、分辨率、按（功能、量程）的隐藏误差、op `range` 都按上文实现；选档不推进虚拟时间。自动量程的判定发生在测量命令里（与真实仪表一致）：`RANGe?` 只报告当前量程，不触发换档。
- 分辨率量化在读数管线里、超量程判定之前：真值 → 隐藏误差 → 校准系数 → 量化 → 超量程判 `overload_value`。
- `table` 映射已加入（instrument-authoring.md 2.3 同步更新）：取不大于设置值的最大表项，没有更小的项时取最小项。
- 输入阻抗见上条的实现；测试 `tests/test_meter_core.py` 覆盖分压电路的两种 `high_z` 取值，与手工计算（含求解器每节点 1e-12 S 的 GMIN 对地电导）差小于 1 ppm。
- `sampler` 协议：#88 已先于本 issue 合并，在核心上给出了最小实现（`sample(index)` / `sample_time_ns()`，`reading_time` 参数）；本 issue 之后，量程系统的读数（量化、超量程、按量程误差）同样进入 `sample()`。耗时的 NPLC 规则（NPLC × 电源周期 + 自动调零 + 开销）与 `smu_core` 的共用实现随 #94。

## 3. 触发模型、读数存储与待完成操作（C2，#88、#94）

### 3.1 `trigger_model`

```yaml
- name: trig
  type: trigger_model
  params:
    sampler: meter
    source: {from: $trigger.source, map: {IMMediate: immediate, BUS: bus, EXTernal: external}}
    trigger_count: $trigger.count
    sample_count: $sample.count
    delay: $trigger.delay           # 秒；"auto" 时由 sampler 按量程与积分时间给出
    buffer: mem
```

状态：`idle` → `initiate` → 等待触发 → 每次触发采 `sample_count` 个读数（各隔 `delay`）→ 共 `trigger_count` 次 → `idle`。op：`initiate`、`abort`、`fetch`（等待完成，返回本次的全部读数）、`read`（`initiate` + `fetch`）、`bus_trigger`。

时间：命令时钟下 `INIT` 立即返回，完成时刻登记为待完成操作（3.3）；`FETCh?` 推进时钟到完成时刻再回答。墙钟下按真实时间。等待 `bus` 触发时 `FETCh?` 不推进时钟，按定义的超时与错误码回答（34465A 的行为由型号写在定义里）。

`external`：本期不接入测试台的触发连线（以后由触发端口与 `media: trigger` 的连线提供，与参考连线同一机制），按“等待外部触发”处理。

### 3.2 `reading_buffer`

一个或多个命名缓冲区：`default`（默认名，2450 写 `defbuffer1`）、`capacity`、`when_full`（`stop` / `overwrite`）。每个读数保存值、时间戳与 sampler 给出的附加字段（源值、状态）。op：`points`、`remove`（取出并删除最早的 n 个）、`read_clear`（`R?`：IEEE 488.2 定长块，块内为逗号分隔的读数）、`data`（按索引范围与 `elements` 选择字段）、`actual`、`clear`、`make`、`delete`。

### 3.3 待完成操作与 `*TRG`

- 组件可以登记“到虚拟时刻 t 才完成”的操作。内置 `*OPC?` 在全部完成时回答 `1`（命令时钟下推进到最晚的 t），`*WAI` 同样等待，`*OPC` 在完成时置 ESR 位 0。示波器采集组件的 `opc` 改为使用这个机制（回复与虚拟时间不变）。校准源的建立时间（第 5 节）用同一机制。
- 内置 `*TRG`：定义中有 `bus_trigger` 操作的仪表回答；没有时按“未建模”处理（D42 不变）。

### 3.5 实现（#88）

已实现：`library/components/trigger.py`（`trigger_model`、`reading_buffer`），`Instrument` 的待完成操作（`register_pending`、`settle_pending`、`wait_until`、`wait_pending`、`arm_opc`），`engine/component.py` 的 `Sampler` 协议。与上文的差别与补充：

- **`Sampler` 协议**：`sample_time_ns()`（一个读数所需的虚拟时间）与 `sample(index)`（此刻的一个读数，`index` 是本次运行中的序号，供噪声流使用）。`dmm_core` 先给出最小实现（参数 `function`、`reading_time`）；NPLC、自动调零与量程的读数时间由 #87 补上，`smu_core` 由 #94 接入；
- 读数在运行完成时按各自的时刻取得（`FETCh?`、`*OPC?`、`*WAI`，或之后任一条命令到达时）；
- 同一条程序消息中，前面命令用掉的时间计入“现在”（`Instrument.now_ns()`），所以 `INIT;*WAI;…` 中 `*WAI` 之后的命令看到的是运行结束后的状态；
- 墙钟下的等待按真实时间（最多 5 s，与链路延迟相同）；步进时钟下不等待，`FETCh?` 在读数完成前报错；
- 没有触发可等时的 `FETCh?`：不回复并报 `waiting_error`（默认 -230），脚本因此超时，与真机一样；`READ?` 配总线或外部触发报 `deadlock_error`（-214）；运行中再 `INITiate` 报 `ignored_error`（-213）；不在等待时的 `*TRG` 报 `trigger_ignored_error`（-211）。错误码都是参数，型号按手册给出；
- `*TRG` 加入内置命令：调用第一个带 `bus_trigger` 操作的组件，没有时仍按“未建模”回复 -113；
- 等待总线触发的运行不登记为待完成操作，所以 `*OPC?` 不会因为一个永远不来的触发而无限等待；
- 读数缓冲区按 `elements` 给出 `READ`（读数）、`REL`（距第一个读数的秒数）与 sampler 附加的字段（#94 起）。

### 3.4 `smu_core` 迁移（#94）

`smu_core` 实现 `sampler` 协议，扫描的每一点也经它产生读数；它自己的缓冲区删除，`sb-smu-1` 改用 `trigger_model` 与 `reading_buffer`，回复、虚拟时间与运行记录逐条不变。源与测量的量程（`source_ranges`、`measure_ranges` 与自动量程）和第 2 节共用量程选择的实现，现在的 21 V / 105 mA 包络成为默认表中的一项。

## 4. 校准存储（C3，#89）

```yaml
- name: cal
  type: calibration_store
  params:
    secured: true
    code: <手册中的出厂安全码>        # 型号的值写在定义里
    key: [$sense.function, meter.range]   # 系数按（功能, 实际量程）分开
    tolerance: 0.1                 # 输入与参考值的偏差上限（参考值的比例）
    ok_reply: "+0"
    fail_reply: "+1"
    volatile_until_store: false
```

- 测量核心以 `store: cal` 引用它，读数 = (原始 − offset[key]) × gain[key]；没有调整过的键为 1 / 0。
- op：`secure`（`ON` / `OFF,<code>`）、`code`（解锁时改安全码）、`value`（参考值）、`adjust`（参考值为 0 调零点，否则调增益）、`store`（计数加一，日期时间取虚拟时钟的日历时间）、`count`、`date`、`time`、`string`。错误码与文字由定义的 `errors` 给出，组件只给默认值。
- 台账字段（计数、日期、字符串）不受 `*RST` 影响。
- `dmm_core` 现有的 `cal_*` op 改为委托给这个组件；`sb-dmm-1` 的定义可以改，回复逐条不变。

### 4.1 实现与设计的差别（#89）

- 键的解析：`$path` 条目取状态值；`component.param` 条目取组件参数，其中**被校核心自己**的 `function` 是当前测量的功能、`range` 是实际使用的量程（自动量程包含在内）。设计示例的 `$sense.function` 也可用（按状态取值），但 DC 与 AC 的读数要各有键，用 `meter.function` 更直接（`tests/models/cal-store-test` 即如此写）。
- 调整的检查：参考值为 0 时（调零点）输入偏差对 `max(|参考值设定|, 1)` 比较——零点本身是 0，比例没有意义，与 `dmm_core` 原来的规则一致；调增益时对参考值本身比较。失败推 702（默认文字与原来逐字相同）并回 `fail_reply`。
- `secured` 与 `value` 可以绑定状态（`$cal.secured`）：读取跟随状态，经 op 写入时写回状态（`internal`），`*RST` 重置状态即恢复保护、参考值回到默认；不绑定时存组件属性。`code` 与台账（`count`/`date`/`time`/`string`）永远是组件属性，`*RST` 不清除。
- 零点系数只修正 DC 读数（调零是直流概念），增益对所有功能生效；AC 的键没有调整过就是 1 / 0。
- `dmm_core` 设了 `store` 时四个 `cal_*` op 全部委托；没有 store 时保留原来的单组系数实现（对不写校准的定义向后兼容）。
- `sb-dmm-1`：`cal.gain`/`cal.offset`/`cal.count`/`cal.date`/`cal.string` 状态删除，`COUNt`/`DATE`/`TIME`/`STRing` 改为存储组件的 op（set 形式按原样回 -221）；`SECure:STATe`/`VALue`/`ZERO`/`GAIN`/`STORe` 命令与回复不变（`ok_reply: "0"`、`fail_reply: "1"` 保持无正号）。`volatile_until_store: true` 保持未存储调整被 `*RST` 丢弃的原行为；已存储的系数与台账现在按设计跨 `*RST` 保留（有意的行为变化，无测试依赖旧值）。

## 5. 校准源与按单位分派的参数（C4，#90）

### 5.1 `calibrator_output`

`role: driver`。参数：`function`（`DCV` / `ACV`，以后扩到电流）、`level`、`frequency`、`operate`、`z_out`、`i_max`、`settle`（可按功能给表）、`standby_above`、隐藏的 `gain_error_ppm` 与 `offset`。

- **待机**：端口不在电路中（断开）；**工作**：带输出阻抗的电压源，交流为单根谱线。
- **过载**：求解后负载电流超过 `i_max` 时转为待机并报错，按工作点判断（与 `smu_core` 的模式切换同一套迭代，D34）。
- **建立**：设定改变或进入工作后，`settle` 秒后达到新值，登记为待完成操作（3.3）。建立期间输出按一阶过渡计算（时间常数 = `settle` / 5），读数因此在建立中途介于新旧值之间；不支持建立过程的型号把 `settle` 设为 0。
- **高电压确认**：新旧设定任一超过 `standby_above` 时，改变设定使仪表回到待机，需要再次进入工作。

### 5.2 按单位分派的参数

`ParamSpec` 新类型 `quantities`：逗号分隔的每个“数值 + 单位”按单位写入设置，并可同时写入别的设置：

```yaml
- header: "OUT"
  params:
    - name: q
      type: quantities
      units:
        V:  {state: out.level}
        A:  {state: out.level, sets: {out.function: DCI}}
        HZ: {state: out.frequency}
      combine:                     # 单位组合决定功能；不在表中的组合报错
        "V":     {out.function: DCV}
        "V,HZ":  {out.function: {if_zero: out.frequency, then: DCV, else: ACV}}
  reply: "{out.level},{out.unit},{out.frequency},HZ"
```

倍率（`mV`、`kHz`）按现有的单位解析；单位缺失、重复或组合不在 `combine` 中时报错（错误码由定义的 `errors` 给出）。静态校验检查单位、状态与组合表。`if_zero` 是唯一的条件写法（`0 Hz` 即直流），不引入表达式。

### 5.3 错误查询的声明

`system` 组件增加 `next_error`、`error_count` op，复用内置错误队列与定义的 `errors`，回复格式由命令的 `reply` 模板给出（`{code},{text}` 等）。这样 `ERR?` 一类写法可以声明，不必挂在 `:SYSTem:ERRor` 下。

## 6. 四线测量与引线电阻（C5，#91）

- **引线配件** `lead`：`IN` / `OUT`，参数 `r`。它不是 `role: series`（那一类阻抗可忽略），端接判断与电路求解都算上它。测试台写 `accessories: [{type: lead, params: {r: 0.5}}]`。
- **浮地 LO**：`smu_core` 增加 `lo` 端口与参数 `floating`（默认 `false`：LO 接测试台地，即现在的行为）。
- **远端检测**：`sense_hi`、`sense_lo` 端口与参数 `remote_sense`。四线时电压环调节 `V(sense_hi) − V(sense_lo)`，读数也取这两个节点；电流仍从 force 端注入。电路上是修正节点分析中的一行受控约束；限值与 CV/CC 切换照常（D34）。二线时 sense 在内部接到 force 端。`remote_sense` 开启而 sense 端悬空时，测试台校验给出警告，运行时按真机的行为处理（型号定义给出读数或错误）。
- 万用表的四线电阻测量不在本期，但 sense 端口的写法与此一致。

### 6.1 实现与设计的差别（#91）

- 电路上不是"一行"而是一个新的求解元素 `loop_source`（`engine/circuit.py`）：约束 `V(sense_a) − V(sense_b) = EMF` 占一个方程，环路的电流以同一个未知量从 force 正端流出、force 负端流回（引线上的压降由此精确出现）。二线与不浮地的单端源都是它的退化形式——`floating: false`、`remote_sense: false` 时它逐位等价于原来的单端理想源，现有 SMU 测试与验收场景不改通过。
- 二线读数以测试台地为参考（`floating: false` 时 LO 在仪表内部接保护地，读数 = V(hi)），浮地时读 `V(hi) − V(lo)`；`remote_sense` 开启且两个 sense 端都接线时读 `V(sense_hi) − V(sense_lo)`。四线的电流环仍然只从 force 端注入/流回，sense 端不参与电流。
- `floating: false` 时 LO 端口若接了线，仪表内部用一个 1e-6 Ω 的连接把它接到测试台地（1 A 下 1 µV，读数 7 位内不可见；取小电阻而不是理想短线，两台 SMU 的 LO 接到同一网络才不会使求解奇异）。
- `remote_sense` 开启而 sense 端悬空（或两 sense 端接到同一网络）：电压环退回 force 端（测试台仍然可解、输出仍然驱动），读数命令报 -221 "Settings conflict; remote sense is on but a sense terminal is not connected"；面板读数（`readings()`，无副作用）显示退回后的值。校验诊断：sense 端悬空给警告 `W-SENSE-UNCONNECTED`，两端接在同一网络给错误 `E-SENSE-SHORT`。
- `sb-smu-1` 的状态与命令：`sense.remote`（`[:SENSe]:REMote[:STATe]`）与 `terminal.floating`（`:SYSTem:FLOATing`），都是 electrical 设置，改动后控制环从设定量自己的环路重新开始；端口 `HI`、`LO`、`SENSE_HI`、`SENSE_LO`。
- 引线与端接：`lead` 的 `role` 为空，`_check_nets` 不把它并入两侧网络——信号源幅度按 50 Ω 负载设定时，隔着引线的 50 Ω 端接不再被看到（与隔着同轴线缆不同，`tests/test_four_wire.py` 有对照用例）；`r` 取 0 时按 1e-6 Ω 求解，不报错。

## 7. 个体偏差按指标抽取（C6，#34）

决定见 D3（第 2 层的起步）。

### 7.1 定义中的 `deviations`

与只作记录的 `specs`（手册原文的指标）分开，`deviations` 是参与计算的部分：

```yaml
deviations:
  - param: meter.gain_error_ppm   # 参数本身就是误差（ppm）：不写 unit，值 = 标称 + 偏差
    by: {function: [DC], range: [0.1, 1, 10, 100, 1000]}   # 可选：按表抽取，每格一个值
    limit: {0.1: 30, 1: 20, 10: 15, 100: 20, 1000: 20}     # 每格的 ±limit；一个数则各格相同
    distribution: uniform          # uniform：±limit 内均匀；normal：σ = limit / coverage，截断在 ±limit
  - param: meter.offset
    limit: 2.0e-6
    unit: V
    distribution: normal
    coverage: 2
```

- `unit` 为 `%` 或 `ppm` 时偏差相对标称值（值 = 标称 × (1 + d)）；不写时 limit 用参数本身的单位，值 = 标称 + d（参数本身就是误差或偏移时这样写）。静态校验检查 `param` 指向已声明的组件与参数、参数是数值，相对偏差用在标称值为 0 的参数上时给出警告。
- 一项的 `limit` 来自手册时，在 `specs` 中引用出处即可，不要求两者自动对应（手册的指标常是“读数的 x ppm + 量程的 y ppm”，换成参数需要领域判断，由写型号的人完成）。

### 7.2 测试台

```yaml
instruments:
  dmm:
    definition: keysight-34465a
    deviation: from_specs          # 按 deviations 抽取
    hidden: {meter: {offset: 0.0}} # 写死的值优先，逐项覆盖
parts:
  r1:
    type: resistor
    params: {r: 1000}
    deviation: {r: {limit: 1, unit: "%", distribution: uniform}}   # 元件的范围属于料号，写在实例上
```

- 抽取用计数器式随机数（`engine/noise.py`），键为（种子、实例名、组件.参数、表键）：与抽取顺序、测试台中其他仪表的增减无关；同一测试台同一种子结果相同。
- 抽到的值写入运行记录头部，回放时校验。
- 界面统一叫“个体偏差”；抽取的值受 `run.reveal_true_values` 约束（写死的值通过 `read_bench` 本来就可读）。

### 7.3 实现（#34）

已实现：`models/deviation.py`（`DeviationRange`、`DeviationSpec`）、`engine/deviation.py`（抽取）。与上文的差别与补充：

- 正态分布截断在 ±limit 之内（超出指标的一台是故障，另用故障注入），因此每个值都在范围内；
- 按表抽取时，值是按 `by` 的键顺序嵌套的表（`{DC: {0.1: …, 1: …}}`），相对偏差按每格自己的标称值计算；#87 的测量核心按（功能、量程）读这张表，每个量程的读数带着各自抽到的增益误差（`test_per_range_deviations_reach_the_reading_of_each_range`）；
- 编辑操作 `set_deviation`（仪表的 `mode`，或 DUT 一个参数的 `range`），画布与 MCP 共用（D9）；
- 会话接口：仪表的 `deviation`（模式）与 `deviations`，DUT 的 `deviations`；每项给出范围、是否抽取、值（只在 `reveal_true_values` 打开时）；隐藏时 DUT 的 `params` 显示标称值。检查器中的“个体偏差”一节显示范围与值，仪表有“按指标抽取”开关；原来 DUT 的“隐藏参数”（`tol_*`）并入这一节；
- 运行记录头部的 `deviations`；回放按记录的种子重新抽取，不一致时警告（例如定义中的范围改过）；
- 诊断：`E-DEF-DEVIATION`、`W-DEF-DEVIATION-ZERO`（定义），`E-DEVIATION-PARAM`、`W-DEVIATION-ZERO`、`W-DEVIATION-NONE`（测试台）；
- `sb-dmm-1`（增益误差、零点）与 `sb-awg-2`（每通道的输出阻抗）写了 `deviations`，供测试与演示。

## 8. 定标判定的真值核对（C7，#92）

脚本照常运行（不 import SimuBench）；结束后：

```bash
simubench verdicts run.jsonl report.csv --map verdict-map.yaml [--json]
```

```yaml
# verdict-map.yaml：说明脚本报告的列
instrument: dmm                 # 被校仪表（测试台实例名），或列名
columns: {nominal: setpoint, reading: reading, low: limit_low, high: limit_high, decision: verdict}
decisions: {PASS: pass, FAIL: fail, "N/A": no_statement}
```

- **对上读数**：报告的每行按顺序匹配运行记录中被校仪表的读数回复（数值相等，按回复的位数比较）；对不上的行明确报出，不猜。
- **真值**：回放运行记录到那条回复的时刻，取被校仪表测量核心的真输入（D37 的探针机制）。判定的对象是被校仪表自己的误差：

  | 量 | 公式 |
  | --- | --- |
  | 脚本看到的误差 | 读数 − 标称值 |
  | 真误差 | 读数 − 真输入 |
  | 两者之差 | 真输入 − 标称值，即参考源与连线的真实偏差 |

  `truth` 用真误差（换算到报告限值的同一形式）对照限值，`assess` 给出 `correct` / `false_accept` / `false_reject` / `withheld`。
- 输出逐点结论与汇总；有误判时退出码为 1。MCP `check_verdicts` 返回同样的结果。
- 核对在运行结束后离线进行，不受 `run.reveal_true_values` 限制；输出只有结论与真误差，不改变运行时的保密规则（`docs/security.md` 说明）。

## 9. 型号不依赖专用组件；写型号的操作手册（C8，#93）

已实现（#93）：`Component.generic`；`siglent_sdg`、`siglent_sdg_channel_limits`、`siglent_sds_acquisition`、`siglent_sds_channel_limits` 标为 `False`；检查在 `tests/test_model_components.py`（例外清单按“定义 id、组件类型”成对列出，另断言目录中所有非通用组件都在清单里，因此也不能新增只是不被内置定义使用的专用组件）。与下面的设计稿的差别：覆盖表草稿的写法是 `simubench coverage <id> --draft <清单>`（`--out` 指定写到哪里，默认在定义旁边，没有定义时为 `instruments/<id>.coverage.yaml`）；定义中已有的命令起草为 `implemented` 并注明“待确认”，其余为 `not_modeled`；补充已有的覆盖表时保留文件开头的注释与已写的条目，其他注释不保留（覆盖表按数据重写）。

- 组件类增加 `generic`（默认 `True`）；现有的型号专用组件标为 `False`，组件目录显示这一项。
- CI 检查：内置定义（带 `reference`）只使用 `generic` 组件。例外清单写在测试里，现在只有两台 Siglent；清单只能缩短（检查同时断言清单中的每一项仍在使用）。
- `.claude/skills/real-model/SKILL.md`：写型号的步骤与命令，规则本身留在 real-models.md。遇到平台缺能力时的做法写在第一屏：不写专用组件，在 #86 下开能力 issue，型号 issue 标为被它阻塞。
- `simubench coverage --draft <命令清单.txt> --model <id>`：由命令头清单生成全部为 `not_modeled` 的覆盖表骨架；已存在的覆盖表只补缺少的命令。
- `.github/ISSUE_TEMPLATE/real-model.md`：真实型号 issue 的模板。

## 10. 分期

| issue | 内容 | 依赖 |
| --- | --- | --- |
| #93 | 型号不依赖专用组件的检查、操作手册、覆盖表草稿（第 9 节） | — |
| #87 | 测量核心：量程、超量程、分辨率、输入阻抗、按量程的误差（第 2 节） | — |
| #88 | 触发模型、读数存储、待完成操作、`*TRG`（3.1–3.3） | #87 |
| #89 | 校准存储（第 4 节） | #87 |
| #90 | 校准源、按单位分派的参数、错误查询的声明（第 5 节） | #88（建立时间） |
| #91 | 四线测量与引线电阻（第 6 节） | — |
| #94 | `smu_core` 接入触发模型与缓冲区、源与测量量程（3.4） | #87、#88 |
| #34 | 个体偏差按指标抽取（第 7 节） | —（按量程抽取在 #87 之后） |
| #92 | 真值核对（第 8 节） | — |
| #7 | Keysight 34465A（只写 YAML） | #87、#88、#89、#93 |
| #8 | Fluke 5522A（只写 YAML） | #90、#93 |
| #12 | Keithley 2450（只写 YAML） | #94、#91、#93 |
| #9 | 定标演示 | #7、#8、#92、#34 |

可以同时开始的：#93、#87、#91、#34、#92；随后 #88、#89；再后 #90、#94。

## 11. 不在本期

- 万用表的电流、电阻、频率等功能，校准源的电流与电阻输出：各自另开 issue，在本期的组件上加功能。
- 外部触发连线（3.1）、换档时间。
- 把 `siglent_sdg`、`siglent_sds_*` 剩下的专用 op 改为声明（第 9 节的例外清单因此缩短）。
- 界面中的定义编辑（与 instrument-authoring.md 相同，仍推后）。
