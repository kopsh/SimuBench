# 真实型号仪表（D42）

目的：用真实型号演示 SimuBench。为某个型号写的脚本（SCPI 写法、`*IDN?` 检查、返回格式解析、错误处理）不改一行即可在虚拟仪表上运行。行为以该型号的编程手册为准，物理量仍由电路求解得到（D13）。

本文是所有型号共同遵守的规则。每个型号一个 issue，按本文实现。

## 1. 命名与身份

- 定义文件：`src/simubench/library/instruments/<厂商>-<型号>.yaml`，id 全小写，如 `keysight-34465a`、`fluke-5522a`。
- `manufacturer`、`model` 与 `identity.idn` 按手册中 `*IDN?` 的返回格式填写，如 `Keysight Technologies,34465A,<序列号>,<固件版本>`。序列号与固件版本的格式也与真机一致。
- 新字段 `reference`：所依据的手册（名称、文档编号、版本或日期、对应的固件版本）。同一型号不同固件的命令可能不同，以此为准。
- 内置仪表只有真实型号（D42）。`sb-*` 是 SimuBench 自造的验证仪表，定义在仓库的 `validation/instruments/`，不随软件包发布；测试与验收通过定义搜索路径加载（见第 9 节）。

## 2. 命令的三种实现程度

手册命令参考中的每条命令，属于以下之一，并记入覆盖表（第 6 节）：

| 程度 | 含义 | 例子 |
| --- | --- | --- |
| 实现 | 语法、参数范围、默认值、返回格式、错误与对测量的影响都按手册 | `:MEASure:VOLTage:DC?`、`:SENSe:VOLTage:DC:NPLC` |
| 接受 | 按手册解析、校验并保存，查询返回保存的值，但不影响仿真 | 显示、蜂鸣器、界面语言 |
| 未实现 | 回复 `-113,"Undefined header;not modeled by SimuBench"`，并在事件历史中标注 | 尚未做的功能 |

未实现的命令必须报错，不能静默接受：脚本在虚拟环境里通过、上了真机却失败，比报错更糟。

## 3. 按手册对齐的细节

- **语法**：长短格式、可省略的节点、数字后缀（通道号）、单位与倍率后缀（`MV`、`KHZ`）、`MIN`/`MAX`/`DEF`、列表与字符串参数的写法。
- **默认值**：`default` 为上电值，`rst` 为 `*RST` 之后的值，与手册的默认值表逐行一致。
- **返回格式**：数字的位数、符号、指数写法（如 `+1.00000000E+00`）、布尔值、枚举的大小写与短格式、多个值的分隔符，都与手册一致。定义中可按命令声明返回格式。
- **错误**：错误码与错误信息文字与手册的错误列表一致，包括设备专用错误码；错误队列长度与溢出（`-350`）也按手册。定义中可声明错误表。
- **触发与时序**：触发系统（`INITiate`、`FETCh?`、`READ?`、`*TRG`、触发源与计数）按手册。测量和命令所需时间（积分时间、建立时间）按手册计入虚拟时间（D12）。
- **状态系统**：`*OPC`、`*WAI`、`*ESR?`、`*STB?`、`*SRE`、`*ESE` 及手册中的操作与可疑状态寄存器。
- **物理量**：测量值来自电路求解与组件模型，不按命令伪造。手册中的精度指标记入定义（可选的 `specs` 字段），供以后由指标生成误差参数（M7）。

## 4. 手册用例

每个型号在 `tests/models/<id>/cases.yaml` 中写测试用例。每条用例是一串命令与预期，注明依据手册的哪一节：

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
      expect_match: '^-222,"Data out of range'
```

一个通用的测试读取所有用例文件，逐条运行（与 `simubench cases` 同一个运行器，`library/coverage.py`）。预期可以是精确字符串（`expect`）、正则（`expect_match`）或数值与容差（`expect_value` + `tolerance`）。用例应覆盖覆盖表中每条“实现”和“接受”的命令；真实型号的每条用例都要写 `manual`（覆盖检查报告缺少的）。

同一种文件也用来写**契约用例**：按既有测试程序的接口契约而不是编程手册编写，`manual` 写契约条目（例如 `examples/lowfreq-cal/cases.yaml`）。步骤可以用 `instrument` 发给测试台中的另一台仪器。运行：

```bash
simubench cases examples/lowfreq-cal/cases.yaml --junit cases.xml   # 失败时给出步骤、命令、预期与实际回复，退出码 1
```

MCP 的 `run_cases` 运行工作区中的用例文件，返回每条用例的结果。

## 5. 演示用的测试台

每个型号至少带一个示例测试台（`examples/benches/`），让它和其他仪表、DUT 连在一起时能测出符合物理的结果。`examples/` 只用真实型号；用 `sb-*` 的测试台放在 `validation/benches/` 或 `acceptance/benches/`（CI 检查）。

## 6. 覆盖表

每个型号一个 `src/simubench/library/instruments/<id>.coverage.yaml`，按手册章节列出全部命令及其程度（实现、接受、未实现）与说明。

- CI 检查覆盖表与定义一致：定义中的命令都在覆盖表中，标为“实现”或“接受”的命令都在定义中，并且都有手册用例。
- `simubench coverage <id>` 打印覆盖表与统计，用于演示时说明做到了什么程度。

## 7. 版权与商标

- 只实现手册描述的行为，不把手册原文复制进仓库；命令名称和简短的语法说明除外。
- `*IDN?` 返回真实的厂商与型号，脚本才能不改一行地运行（D5）；发布版也如此（D42）。
- 只以名称指明所模拟的型号：界面与文档不使用厂商标志、配色等商业外观，不暗示厂商认可。
- 声明：SimuBench 与所模拟型号的厂商没有关联，也未获其认可；文中的厂商名与型号是各自所有者的商标，仅用于指明所模拟的仪表。

## 8. 字段说明（框架已实现）

以下字段都是可选的，`sb-*` 定义不用改。带 `reference` 的定义即为真实型号；内置定义都必须带 `reference`（`tests/test_release.py`）。

| 字段 | 位置 | 说明 |
| --- | --- | --- |
| `reference` | 定义 | 所依据的手册：`title`（名称）、`document`（文档编号）、`version`（版本或日期）、`firmware`（对应的固件版本）。有此字段时：未定义的命令回复 `-113,"Undefined header;not modeled by SimuBench"`，事件历史中该错误带 `reason: not_modeled`；错误信息只用手册文字，不附带 SimuBench 的诊断细节（细节仍记入事件历史）。 |
| `errors` | 定义 | 错误表 `{错误码: 信息文字}`。覆盖标准文字，也可新增设备专用错误码（如 `501`）；`0` 的文字用于“无错误”。 |
| `error_queue` | 定义 | 错误队列长度（默认 20）。队列满时最后一格变为 `-350`（SCPI-99 21.8），其文字也可在 `errors` 中给出。 |
| `format` | 定义 | 默认返回格式：`number`（`nr3` / `nr2` / `nr1` / `engineering`，后者即 `200E-3`）、`digits`（小数位数）、`sign`（非负数是否带 `+`）、`exponent_digits`、`integer`（`plain` / `signed` / `engineering`）、`boolean`（`numeric` 即 1/0，`word` 即 ON/OFF）、`enum`（`short` / `long`）、`error_code`（`:SYSTem:ERRor?` 中的错误码是否带 `+`，如 Keysight 的 `+0,"No error"`）。默认值即 `sb-*` 一直以来的格式（`+1.000000E+03`）。 |
| `commands[].format` | 命令 | 覆盖该命令的返回格式，字段同上。`enum` 还可以是 `as_defined`：按定义中的写法原样返回（如 Siglent 的 `RISing`）。 |
| `builtins` | 定义 | 这个型号回答的内置命令（如 `["*IDN", "*OPC", "*RST"]`）。不写时全部回答；写了以后，其余内置命令（`*CLS`、`:SYSTem:ERRor?` 等）按“未建模”处理（D45）。 |
| `display.actions` | 定义 | 界面的面板操作发送的命令，如 `{single: ":TRIGger:MODE SINGle"}`；不写时“单次”发送 `:SINGle`。 |
| `specs` | 定义 | 手册中的精度指标，任意结构；目前只保存，不参与计算（M7）。 |
| `unknown_commands` | 定义 | `error`（默认）：未定义的命令回复 `-113`；`ignore`：不报错、不回复，事件历史记为 `ignored`。只用于按接口契约编写、没有 `reference` 的定义（D42），真实型号不允许。 |
| `commands[].header` 为 `*XXX` | 命令 | 定义可以给公共命令自己的行为（如方言中的 `*CLS?`），优先于内置的公共命令。 |
| `idn` | 测试台实例 | 覆盖定义的 `*IDN?` 回复，字段同 `identity.idn`；用于主机程序校验的型号串。 |
| `dialect` | 定义 | 非 SCPI 方言的应答框架：`echo_header`（`off` / `short` / `long`，或绑定一个取值为 OFF、SHORT、LONG 的状态，如 LeCroy 的 `COMM_HEADER`），`multipliers`（无单位数值的倍率后缀，如 `MSIZ 10K`）。默认即 SCPI。见 [instrument-authoring.md](instrument-authoring.md) 2。 |
| `commands[].reply` | 命令 | 查询应答模板：`{header}`（按 `echo_header` 回显的命令头）、`{value}`（绑定的状态，或操作的应答）、`{<状态路径>}`，如 `"{header} {value} V"`。 |
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
      - {header: ":DISPlay[:STATe]", level: accepted}
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

回复是二进制块（如波形数据）的查询写 `binary: true`：按块头给出的长度读取，`expect` / `expect_match` 作用于按 Latin-1 解码的字节（包括块后的换行）。

每条用例在一个刚上电的测试台上运行，测试报告中单独列出，名称带手册章节。

**完整示例**：`tests/models/acme-dm1/` 是一个虚构的“真实型号”，只用于测试框架本身（不在产品仪表库中）：定义（`reference`、`errors`、`error_queue`、`format`、`specs`、命令级 `format`、没有冒号层级的 `OUT` 命令）、覆盖表与用例。`simubench coverage acme-dm1 --definitions tests/models/acme-dm1` 打印它的覆盖表与统计。

**第一个产品型号**：`siglent-sds2104xplus`（D45）。覆盖表由手册正文中的命令条目生成（622 条），实现与接受的级别和说明逐条手写；`examples/scripts/siglent_sds_read_waveform.py` 按手册的波形读取示例编写，在真机和 SimuBench 上都能直接运行（`tests/test_siglent_sds.py`）。

**第二个产品型号**：`siglent-sdg2042x`（D45）。SDG 的命令不是 SCPI 树，而是 Siglent 自己的键值写法（`C1:BSWV WVTP,SINE,FRQ,1000`），回复带命令头与单位（`C1:BSWV WVTP,SINE,FRQ,100HZ,...`），所以这些命令都是组件操作（`library/components/siglent_sdg.py`），由组件拼出完整回复。手册的命令头写法也不全是 SCPI 的长短格式：`ParaCoPy` 的短格式是 `PACP`，定义中写成 `PAraCoPy`，使大写字母就是短格式。

## 9. 内置定义与验证定义

- **内置**：`src/simubench/library/instruments/` 只放真实型号的定义与覆盖表，随 wheel 发布，是元件库、MCP `list_catalog` 与命令行默认列出的全部仪表。
- **验证**：`sb-*` 定义在 `validation/instruments/`，用到它们的测试台在 `validation/benches/`（开发时用的演示台）与 `acceptance/benches/`（验收场景）。只被它们使用的组件（锁相、频谱仪、SMU、电子负载、射频源、PRBS）留在引擎里。
- **搜索顺序**：测试台所在目录的 `instruments/`、工作区的 `instruments/`、环境变量 `SIMUBENCH_DEFINITIONS` 中的目录（多个目录用 `os.pathsep` 分隔，Linux 为 `:`，Windows 为 `;`），最后是内置定义。
- **加载验证定义**：`pytest` 由仓库根目录的 `conftest.py` 自动设置 `SIMUBENCH_DEFINITIONS`；开发时 `simubench serve --workspace validation`（`validation/` 本身就是一个带 `instruments/` 的工作区），或 `SIMUBENCH_DEFINITIONS=validation/instruments simubench serve ...`。
- **检查**：`tests/test_release.py` 检查内置定义都是真实型号、未设置搜索路径时仪表库中没有 `sb-*`、`examples/` 中没有 `sb-*` 引用；CI 的打包任务检查 wheel 中没有 `sb-*` 定义。
