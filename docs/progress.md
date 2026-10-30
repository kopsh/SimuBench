# 开发进度

按 roadmap.md 的里程碑记录进度与自检结果（D24）。

| 里程碑 | 状态 |
| --- | --- |
| M0 基础 | 完成（内部软件包源发布推迟到 M5） |
| M1 第一条纵向切片 | 完成，待可用性验证 |
| M2 编辑、校验与 Agent | 完成（可用性验证待安排） |
| M3 仪表扩展 | 完成：锁相放大器、频谱分析仪、射频信号源、SMU 场景与各自屏幕；示波器换型（`sb-dso-2x`） |
| M4 可观测性 | 完成：运行记录独立回放与采集配方（D36）、界面中的运行记录（时间线、回放并定位第一处不同）、虚拟探针与“为什么是这个值”（D37）完成；读数为 0 V 时沿电路向上游追溯原因（未接线、源输出关闭、设定为 0、上游无源、故障），示波器屏幕按通道显示（`engine/silence.py`）；运维侧：结构化日志、版本清单、doctor、诊断包（D38）完成；指标导出（`/api/metrics`，Prometheus 文本格式）与画布上的保真度标注（≈）完成；运行中的画布反映信号与活动（连线电平、示波器缩略图、命令闪烁、顶栏时钟状态，D44）完成；内部遥测收集服务属于内部发布（M5） |
| M5 内部发布与定标场景 | 进行中：定标基础能力完成（D39，见 calibration-notes.md 末节）；定标场景由产品负责人验收；pytest 插件、格式迁移、首次使用引导完成（D40）；带界面的 wheel 安装（CI 中在干净环境安装并自检）、性能与稳定性预算测试、安全自检（docs/security.md）完成；内部遥测收集服务待定（需内部服务地址） |

## M0 基础

| 完成条件 | 自检 |
| --- | --- |
| 仓库结构：引擎、服务、前端、参考计算、验收测试 | ✅ `src/simubench`、`web`、`refcalc`、`acceptance` |
| Windows 与 Linux 的 CI | ✅ `.github/workflows/ci.yml`（Python 与 Web 两个平台矩阵 + 端到端） |
| 代码规范与检查 | ✅ ruff（检查与格式）、ESLint、TypeScript 严格模式 |
| Pydantic → OpenAPI / JSON Schema → TypeScript 生成流水线与一致性检查（D21） | ✅ `simubench schemas`、`web/scripts/gen-api.mjs --check` |
| 品牌基础：文字标志、主色与功能色、字体 | ✅ 设计令牌（`web/src/styles/globals.css`），字体随包离线提供 |
| 术语表与文案约定 | ✅ `docs/glossary.md` |
| 信息架构与工作台布局 | ✅ `docs/ui-design.md` |
| 中英文文本外置 | ✅ `web/src/i18n` |
| 基础组件库（Storybook） | ✅ `pnpm storybook`：设计令牌、基础组件、仪表面板部件、示波器画面、状态视图，工具栏切换主题与语言；视觉回归 `pnpm visual`（每个故事的亮/暗两种截图，CI 中运行） |
| 内部软件包源发布流程 | ✅ 带界面的 wheel（`scripts/build-wheel.sh`，CI 中安装自检）；⏳ 上传到内部软件包源需要其地址与凭据 |

## M1 第一条纵向切片

| 完成条件 | 自检 |
| --- | --- |
| 组件模型核心：组件、端口、连线、网络、线性网络求解 | ✅ 按频率的节点分析（`engine/circuit.py`） |
| 信号描述与求值 | ✅ 谱线与逐点采样；长时间运行下相位精确 |
| 虚拟时钟：命令驱动、步进、墙钟 | ✅ |
| 动作队列与运行记录 | ✅ 串行执行；JSON Lines 记录，原始字节完整保存 |
| 通用 SCPI 命令层与 IEEE 488.2 基础 | ✅ 长短格式、可选节点、数字后缀、复合命令、标准错误、状态字节 |
| 函数发生器、示波器、RC 低通、探头 | ✅ `sb-awg-2`、`sb-dso-4`；幅度按设定负载定义（D13） |
| PyVISA 后端（进程内） | ✅ 资源名支持虚拟地址、真实测试台地址、实例名 |
| SCPI 网络仪表、`simubench serve` | ✅ |
| 独立参考计算与首批验收测试 | ✅ 示波器场景与多通道场景共 24 项 |
| 未修改的 PyVISA 脚本只换资源管理器即可运行 | ✅ 验收测试即按此方式编写 |
| 两个平台的测试 | ✅ 本地 Linux 通过；CI 覆盖 Windows |
| 界面：测试台列表、画布（只读）、示波器屏幕、函数发生器面板、通信记录、断线重连 | ✅ 端到端测试 6 项 |
| 界面质量标准逐条自查 | 见下表 |
| 可用性验证（5 位内部工程师） | ⏳ 需要产品负责人安排，验证完成后一并进行 |

### 界面质量自查（M1 范围）

| 标准 | 结果 |
| --- | --- |
| 设计令牌、浅色/深色主题 | ✅ |
| 通道颜色在卡片、迹线、连线、图例一致 | ✅ |
| 状态用文字 + 颜色表达 | ✅ 输出开关、触发状态、连接状态 |
| 工程单位与等宽数字 | ✅ |
| 空状态、加载、错误、断线只读、令牌无效 | ✅ |
| 命令面板与键盘操作 | ✅ ⌘K / Ctrl K；输入框回车提交、Esc 取消、历史命令上下键 |
| 危险操作确认 | ✅ 打开输出需确认 |
| 通信记录虚拟滚动 | ✅ |
| 本地服务安全：令牌（可选，默认关闭，D43）、Host/Origin 校验、仅本机监听 | ✅ 有测试 |
| 中英文切换 | ✅ |
| 撤销/重做、画布编辑、自动保存与版本 | ✅ 见 M2（D25、D35） |
| 视觉回归测试 | ✅ `pnpm visual`（Storybook 每个故事亮/暗两种截图，CI 中运行） |

## M2 编辑、校验与 Agent

| 完成条件 | 自检 |
| --- | --- |
| Bench 编辑操作（引擎中实现，画布与 MCP 共用，D9） | ✅ `engine/edit.py`：14 种操作，全部成功或全部不生效 |
| 校验诊断：代码、JSON 指针、说明、建议 | ✅ `engine/validate.py`；`simubench validate`；画布与问题面板定位 |
| 多通道（D15） | ✅ M1 已完成，多通道场景验收通过 |
| 故障注入：通信层与仪表层（另含组件层） | ✅ `engine/faults.py`（D27）；API、界面、MCP；可预设在测试台文件中 |
| 墙钟模式 | ✅ 引擎支持；延迟故障在墙钟下真实等待（上限 5 s）；界面时钟按真实时间走、屏幕自动刷新，只读读数取当前真实时刻；端到端测试（锁相放大器不推进时钟即建立） |
| MCP：定义库、创建与编辑、校验、由脚本生成、部署、发送 SCPI、通信记录 | ✅ `simubench mcp`（D26），协议级测试 9 项 |
| 示波器场景全部 | ✅ 第 1–7 组；第 7 组按 D29 调整；第 8 组“采集保存为配方” ✅（D36）；第 9 组换型 ✅（`sb-dso-2x`，见 validation-scope.md）|
| 多通道场景 | ✅ |
| 画布编辑：添加仪表与 DUT、连线、配件、删除、撤销/重做、自动保存 | ✅ 元件库拖放；端口拖动连线；Delete 删除；Ctrl+Z / Ctrl+Shift+Z |
| 版本 | ✅ 以文件为准，乐观并发与外部修改载入（D25）；版本历史：每次保存一个版本并记录来源（界面、Agent、外部修改、恢复），连续移动节点合并，恢复不丢失任何版本（D35） |
| 校验诊断在画布上定位 | ✅ 节点与连线标记，点击问题选中对象 |
| Agent 修改的差异审阅 | ✅ 版本历史对话框：逐项差异（新增、删除、修改的仪表、DUT、连线与字段的新旧值），可对比上一版本或当前文件；外部修改提示可直接打开；端到端测试 |
| 故障注入面板、参数编辑 | ✅ |
| 仪表卡片自动生成（类别模板 + 通用布局） | ✅ M1 已完成 |
| 端到端测试 | ✅ 12 项（编辑 5 项、故障 1 项） |
| 可用性验证 | ⏳ 与 M1 一并安排 |

完成条件“Agent 可由一份脚本生成 Bench 并部署，工程师在画布上审阅后运行”：`draft_bench_from_script` → `start_session`，会话立即出现在同进程的 Web Bench 中，工程师在编辑模式下补全 DUT 与连线后重新启动。已用 stdio 子进程端到端验证。

## M3 仪表扩展

| 完成条件 | 自检 |
| --- | --- |
| 锁相放大器 `sb-lia-1` | ✅ 内部/外部参考、谐波、1–4 阶滤波、精确建立（D30）、ENBW 噪声、过载 325/326、失锁 327、模型边界 329、自动增益/相位、SNAP? |
| 万用表 `sb-dmm-1`、任意波源 `sb-arb-1` | ✅ 万用表仅电压功能；电流、电阻随 SMU 一起补 |
| 锁相放大器场景全部 | ✅ 20 项验收测试（第 1–6 组）；差异见 validation-lockin.md |
| 锁相放大器屏幕（读数与建立曲线） | ✅ 由类别模板生成；读数接口不经过 SCPI（D32）；端到端测试 |
| 新仪表的界面由仪表定义自动生成 | ✅ 无类别模板的仪表使用通用读数屏幕 |
| 频谱分析仪 `sb-sa-1`、PRBS 源 `sb-prbs-1` | ✅ 设置耦合、四种检波器、三种迹线模式、标记与计数器、外部参考、单次扫描（D33） |
| 频谱分析仪场景全部 | ✅ 17 项验收测试（第 1–7 组）；差异见 validation-specan.md |
| 频谱仪屏幕（迹线、标记） | ✅ 窗口、RBW、扫描时间经 SCPI 设置，由仪表执行耦合规则；端到端测试 |
| 射频信号源 `sb-sg-1` | ✅ AM 精确、FM/PM 贝塞尔谱线（卡森规则）、列表与步进扫描、隐藏的时基偏差经 REF_OUT 传递、拒绝规则 |
| 射频信号源场景全部 | ✅ 14 项验收测试（第 1–6 组）；差异见 validation-rfgen.md |
| SMU `sb-smu-1`、电子负载 `sb-el-1` | ✅ 分段线性器件（D34）：四象限限值、CV/CC/OFF、TRIPped?、状态位 8、状态寄存器 16384；NPLC 读数时间；线性/对数/列表扫描；缓冲区；21 V 以上 105 mA；电子负载恒流（拐点）与恒阻 |
| SMU 场景全部 | ✅ 16 项验收测试（第 1–5 组）；差异见 validation-smu.md |
| SMU 屏幕（I–V 曲线、缓冲区表）、电子负载与射频信号源面板 | ✅ 由类别模板生成；限值线与受限点高亮；端到端测试 |
| 参考与时基连线 | ✅ 仪表定义用 `ports.media` 标出参考端口（REF_IN、REF_OUT）；画布上参考连线为点划线、参考色并标 REF |
| 非线性求解（带限值的源、电子负载） | ✅ 分段线性器件（D34） |
| FFT 数值路径 | 不需要：周期信号全部用精确谱线表示（D33） |

## 新增的实现决定

开发过程中按 D24 自行做出的实现选择：

- **网络求解**：线性部分采用按频率的修正节点分析，每个节点对地加 1e-12 S 电导（与 SPICE 相同），使悬空节点可解。
- **相位精度**：时间以整数纳秒保存，相位按“整秒 + 余数”分别计算，长时间运行后相位不丢精度。
- **空闲推进**：命令驱动时钟下，10 ms 以下的空闲视为脚本自身开销，不推进虚拟时间；更长的空闲按 1 ms 取整推进并写入运行记录（architecture.md 待定问题 4 的处理方式）。
- **示波器**：自有型号 `SB-DSO-4`，波形默认小端字节序，支持 `:WAVeform:BYTeorder` 切换；交流耦合与带宽为模拟一阶滤波（物理正确，D13）。
- **界面布局**：画布节点位置写在测试台文件的 `layout` 中，两种模式下拖动都会保存，不触发重启提示（D25）。

## 真实型号（D42）

| 完成条件 | 自检 |
| --- | --- |
| 框架：定义的新字段（`reference`、`errors`、`error_queue`、`format`、命令级 `format`、`specs`） | ✅ `tests/test_real_models.py`：错误表文字、设备专用错误码、队列溢出、返回格式、未实现命令的回复与事件标注；`sb-*` 行为不变 |
| 覆盖表与一致性检查、`simubench coverage <id>` | ✅ 不一致时指出具体命令 |
| 手册用例与通用运行器（PyVISA 后端，每条用例单独报告并带手册章节） | ✅ 虚构型号 `tests/models/acme-dm1` |
| IEEE 488.2 语法：数字与单位间空格、多个带单位的参数、没有冒号层级的命令头 | ✅ 已支持，补充测试 |
| Siglent SDS2104X Plus（D45，替代 InfiniiVision 3000T） | ✅ 手册用例 24 条；覆盖表 622 条（实现 36、接受 13、未实现 573）；按手册示例读取波形并与网络信号比对（8/10 位、BYTE/WORD）；示例测试台 `siglent_scope_rc` |
| 示波器噪声、抖动与量化（D46，issue 24） | ✅ `acceptance/test_noise_scenario.py`：0.5 / 1 mV/div、8 / 10 位下噪声 σ 与 `refcalc.noise` 一致（按样本数的 4σ 区间）；平均 16 次按 1/√16 下降；探头比例；每次采集不同、重新上电后相同；带噪声的运行记录逐位重放 |
| 示波器自动测量：顶值/底值、参考电平迟滞测频、不可测返回（F2，issue 131） | ✅ AMPLITUDE 按直方图取上下半的众数电平（正弦不再 ≈ 0.707×PKPK，方波即顶底电平；斜坡/三角无峰时退回极值）；FREQ/PER 在 10 %/50 %/90 % 参考电平的迟滞带上计完整周期、按穿越时刻最小二乘求周期（20 dB 信噪比含 8 位量化误差 < 0.01 %，中线噪声不再产生假过零）；不足一个周期返回定义声明的无效值与状态（SDS 的 `:MEASure:SIMPle:VALue?` 按定义的 `invalid_value`/`status`，声明为 9.9E37）。`tests/test_siglent_sds.py` 四条验收 + `tests/test_scope_acquisition.py` 边界用例 |
| Siglent SDG2042X（D45，替代 33500B） | ✅ 手册用例 11 条；覆盖表 193 条（实现 7、接受 2、未实现 184）；负载设定（HZ / 50 Ω）与实际负载（1 MΩ / 50 Ω）四种组合下示波器读到的幅度与手工计算一致；占空比、对称度、反相；示例测试台 `siglent_scope_rc` 改为两台 Siglent |
| 内置仪表只有真实型号，`sb-*` 不发布（issue 35） | ✅ `sb-*` 定义移到 `validation/instruments/`，演示台移到 `validation/benches/`；定义搜索路径 `SIMUBENCH_DEFINITIONS`，测试经 `conftest.py` 加载；服务启动会话与回放运行记录也搜索工作区的 `instruments/`（此前只有校验搜索）；`tests/test_release.py` 与 CI 打包任务检查内置定义、`examples/` 与 wheel 中没有 `sb-*` |
| 型号：Keysight 34465A、Fluke 5522A、Keithley 2450 | ⏳ 各自的 issue |

## 低频校准工作台（lowfreq-cal.md）

| 交付物 / 验收标准 | 自检 |
| --- | --- |
| D1 参考示波器（LeCroy 风格方言）、D2 示波器校准仪、D4 工作台预设、D5 示例脚本与说明 | ✅ `examples/lowfreq-cal/`；按接口契约编写，不带 `reference`（D42） |
| D3 私有文本波形回读 `C<n>:INSPECT? "DATA_ARRAY_1",FLOAT` 与 `WAVEFORM_SETUP` 分块 | ✅ 通用组件 `waveform_transfer` 与 `scope_acquisition` 的配置（#48 起没有专用代码）；回复格式、`FP`/`NP`/`SP`、任意点数拼接（`tests/test_lowfreq_cal.py`） |
| 平台：`unknown_commands: ignore`、定义中的公共命令、测试台实例的 `idn`、`output_stage` 的 `hiz_from`、`coax_cable` 的趋肤效应 | ✅ 单元测试；schemas 与前端类型已重新生成 |
| AC-1 主机流程经 raw socket 完成；AC-2 30 000 / 100 000 点；AC-3 频响合法且单调；AC-4 同种子一致 | ✅ `acceptance/test_lowfreq_cal_scenario.py`（pyvisa-py，真实套接字） |
| AC-5 未启用平台时真实硬件路径不受影响 | ✅ 主机程序与其配置不被修改，只换地址 |
| `*IDN?` 由用户自定义（主机脚本校验 IDN） | ✅ 测试台实例的 `idn`，可写整串或带 `{serial}` 等字段；`test_bench_idn_overrides_the_definition` |
| 用真实主机程序跑一遍 | ⏳ 需要主机程序；验收测试以按契约编写的示例脚本代替 |

## 由用户与 Agent 搭建仪表（instrument-authoring.md，issue 39）

| 完成条件 | 自检 |
| --- | --- |
| 定义的静态校验：`simubench validate` 接受定义文件，错误码 `E-DEF-*`、JSON 指针、建议（issue 40） | ✅ `engine/validate_definition.py`；仓库中全部定义无诊断；每类错误一个反例（`tests/test_validate_definition.py`）；检出 `sb-sa-1` 的 `:TRACe[1]:TYPE`（`TRAC1:TYPE` 不匹配），改为 `:TRACe<t>:TYPE` |
| 组件角色：`role: driver`、`role: series`，测试台校验不再写死组件名（issue 40） | ✅ 内置组件已声明；测试台诊断不变（全部测试台前后逐条比对）；自定义组件的驱动与串联测试 |
| 声明式命令与应答：`dialect`（命令头回显、倍率后缀）、`format` 的 engineering、`reply` 模板、多状态命令与 `pairs`、`ratio`、组件参数映射（issue 42） | ✅ 低频校准工作台的设置命令、`TRSE`、`MSIZ`、`WAVEFORM_SETUP` 与校准仪全部改为声明，`calibrator.py` 删除；`tests/test_lowfreq_cal.py` 与验收不改即通过；新字段的运行与静态校验测试（`tests/test_declarative.py`） |
| 通用波形传输 `waveform_transfer`：文本、ASCII、定长块；选点；ADC 码的比例、偏移、位数、字宽、字节序、无符号（issue 43） | ✅ sb-dso-2x/4、Siglent SDS、低频校准参考示波器改为配置；`:WAVeform:DATA?` 各格式（WORD LSB/MSB、有符号/无符号、BYTE、ASCii、Siglent 8/10 位、选点）与改动前逐字节一致（步进时钟下比对）；`tests/test_waveform_transfer.py` |
| 可配置的示波器采集 `scope_acquisition`：记录长度规则、触发（可按通道绑定）、运行控制、何时重新采集、ADC、噪声（issue 44） | ✅ Siglent SDS 与低频校准参考示波器的行为改为定义中的参数，专用组件只剩专用命令；三台示波器约 100 条命令的回复、虚拟时间与采集事件与改动前逐项一致（命令时钟），唯一差别是 Siglent 平均采集的耗时改为 N 次（D46）；参考示波器改用通用组件后运行控制不变（`tests/test_scope_acquisition.py`） |
| 组件目录：`GET /api/components` 与 MCP `list_components`（含内部组件，端口、参数、op、电路角色），`get_definition` 补充组件、内部连线与端口（issue 41） | ✅ 由组件类生成（参数说明取自 `defaults` 旁的注释，op 参数取自签名）；每个注册的类型都在目录中、op 与静态校验所见一致（`tests/test_component_catalog.py`）；只凭目录写出的最小定义通过静态校验（`tests/test_mcp.py`） |
| 用例运行：`simubench cases`（JUnit 报告）与 MCP `run_cases`，手册用例与契约用例同一种文件（issue 45） | ✅ `tests/test_real_models.py` 改用同一个运行器，69 项结果不变；低频校准工作台的契约用例 11 条通过（`examples/lowfreq-cal/cases.yaml`）；失败信息、刚上电、JUnit 可解析、MCP（`tests/test_cases.py`、`tests/test_mcp.py`）；契约用例查出并修正 `FP` 超出记录时多一个空格的回归 |
| MCP 编写仪表定义：`read_definition`、`create_definition`、`edit_definition`（JSON Patch，sha256 乐观并发，版本历史标为 Agent）、`validate_definition`、`draft_definition_from_script`；会话提示“重新启动以应用”（issue 46） | ✅ 从只用契约命令的脚本出发：起草 → 首次运行用例 3 条失败 → 补丁（枚举选项、通道数、型号）→ 校验无诊断 → 用例 4 条全过（`tests/test_mcp.py`）；冲突、坏补丁、只读的库定义、会话的 `stale_definitions`；JSON Patch 与类型推测（`tests/test_definition_authoring.py`） |
| 验收：只用 YAML、再只用 MCP 重建低频校准工作台（issue 48） | ✅ `lecroy.py` 删除，两份定义只用通用组件；补充 `when`、索引参数、操作应答字段、`system` 组件、命名测量、记录描述、`set_identity`（instrument-authoring.md 2.5）。参考示波器约 35 条命令的回复、虚拟时间与采集事件与改动前一致；从空工作区只用 MCP 搭出的工作台指纹与示例相同，`tests/test_lowfreq_cal.py` 与验收场景在它上面全部通过（`tests/test_lfcal_mcp_rebuild.py`）。没有需要插件的部分 |
| 插件出口 | ✅ issue 47（见下文“插件出口”一节） |

## 仪表内部组件图（Issue 20，D3）

| 完成条件 | 自检 |
| --- | --- |
| 只读接口 `GET /api/sessions/{sid}/instruments/{name}/internals`：组件（名称、类型、所属通道、说明、标称参数与绑定的设置）、内部连线、面板端口映射 | ✅ `tests/test_internals.py`：每个仪表定义的组件、连线、端口与定义文件一致；绑定参数展开为当前设置；个体偏差不出现；单通道仪表；HTTP 404 |
| 界面：仪表检查器与命令面板的“查看内部结构”打开标签页；沿信号流向分列、按通道分行，未展开的通道折叠为一组；点击组件看类型、参数与端口；只读 | ✅ 端到端测试（示波器的输入端口 → 前端 → 采集，展开全部通道）；布局单元测试；Storybook 故事与亮/暗视觉基线 |

## HiSLIP 接入（Issue 3，D41）

| 验收标准 | 自检 |
| --- | --- |
| pyvisa-py 经 `TCPIP0::127.0.0.1::hislip<i>,<端口>::INSTR` 连接：`*IDN?`、含 0x0A 的波形块完整读回、`read_stb()`/`clear()` 与进程内后端一致、独占锁期间其他会话写入被扣住 | ✅ `acceptance/test_hislip_scenario.py`（4 项）与 `tests/test_hislip.py`（锁等待与超时失败、释放后继续） |
| 原始协议客户端覆盖 pyvisa-py 测不到的部分：会话建立（错误子地址 FatalError）、最大消息长度拆分、查询被打断、设备清除握手、SRQ、未知消息类型 | ✅ `tests/test_hislip.py`（20 项），含空子地址开默认仪表、`hislip_<实例名>` 别名、锁信息查询、断连即释放锁、LinkDown/timeout 故障 |
| 示波器场景参数化，SOCKET 与 HiSLIP 两种连接结果一致 | ✅ `acceptance/test_scope_scenario.py::test_amplitude_follows_circuit_theory[socket|hislip]` |
| HiSLIP 端口只绑定 127.0.0.1 | ✅ `test_scpi_sockets_listen_on_loopback_only` 扩展覆盖 `address_hislip` |
| `scripts/check.sh` 通过；schemas 与前端类型最新 | ✅（提交前运行） |
| NI-VISA / Keysight IO Libraries 手动验证 | ⏳ 不作为合并条件，待有环境后进行 |

## 示波器面板的运行 / 停止 / 单次不经过 SCPI（Issue 32，D32）

| 验收标准 | 自检 |
| --- | --- |
| 开着“运行”时“通信”没有新增条目；“事件”只有一条运行与一条停止 | ✅ 面板操作接口 `POST /api/sessions/{sid}/instruments/{name}/panel`，在仪表内部执行，记为 `panel` 事件；运行中的屏幕画面不产生任何事件、不推进虚拟时间（`tests/test_panel.py`）；端到端：通信中没有 `:SINGle` / `:TRIGger:MODE SINGle`，事件中有“面板: 单次”“面板: 运行”（`web/e2e/workbench.spec.ts`） |
| 界面运行期间，脚本的命令、回复与虚拟时间与界面没开时相同 | ✅ 脚本的命令之间插入屏幕刷新，回复哈希与虚拟时间逐条相同（`test_a_running_screen_does_not_change_what_a_script_sees`） |
| 界面“单次”后脚本读 `:WAVeform:DATA?` 得到这次采集 | ✅ Siglent：读回的块等于这次采集的编码，采集次数不变（停止的示波器交出这条记录）；sb 示波器没有单次模式时为一次 `digitize`，停止时也采集（顺带修正 #44 起停止状态下 `:DIGitize` 不采集） |
| 运行时噪声逐帧变化；含面板操作的运行记录逐字节回放 | ✅ 画面的噪声按帧号取值，与采集的噪声互不影响；回放重新执行面板操作，回复全部一致 |
| 去掉“实时预览 / 最近采集”切换 | ✅ 屏幕显示仪表当前显示的内容：运行中、已停止、等待触发（单次已就绪、尚未触发） |
| 端到端测试与视觉回归截图更新 | ✅ 端到端测试改为面板语义；示波器屏幕不在 Storybook 中，视觉截图不变 |

## 界面去掉“运行”概念（Issue 31）

| 验收标准 | 自检 |
| --- | --- |
| 除示波器面板 RUN/STOP 外，中英文文案不再出现“运行”测试台的说法 | ✅ 以 grep 全量清扫 `web/src/i18n`（保留示波器语义与 simubench serve 进程的“运行”）；等价英文键同步（Operate/Build、Records、Close bench） |
| 打开即可操作、脚本即可连接；关闭、重置、切换（含确认框）、搭建切回自动重建有端到端测试 | ✅ `web/e2e/bench-open.spec.ts`（5 项）与 `edit.spec.ts`（自动重建、不合法定义阻止切换）；服务端 `tests/test_server.py`（reset 同 id 回初始态、连接脚本列出、只打开不产生记录） |
| 只打开不操作不产生记录；有操作的记录仍可逐字节回放 | ✅ `Recorder` 延迟到首个动作建文件（header 随首事件落盘）；既有回放测试不变 |
| Storybook 与视觉回归截图（亮/暗）更新 | ✅（提交前运行；本项未改动故事内容） |

## 测试可复现：空闲时间不计入虚拟时间（Issue 17，D47）

| 验收标准 | 自检 |
| --- | --- |
| 命令间插入真实延迟（忙等 50 ms 模拟慢机器）：`sleep`/`off` 与无延迟完全一致，`wall` 按原有行为变化 | ✅ `tests/test_idle.py::test_reproduce_the_drift_of_issue_4`（drift 满漂移 1 s 后 `r`：三档各跑快慢两遍，`sleep`/`off` 逐值相等且等于 1500，`wall` 慢机 > 1520） |
| `sleep` 语义：pytest 插件下 `time.sleep(2)` 与 `bench_session.advance(2)` 读数相同，真实耗时 < 0.1 s | ✅ `tests/test_pytest_plugin.py::test_sleep_is_virtual_time_by_default`（锁相放大器读数逐字符串相等；真实耗时断言 < 0.1 s） |
| 不影响已有测试：全部现有测试不改通过 | ✅ `scripts/check.sh` 468 项通过；无测试标注 `idle=wall`（现有测试均以显式 `advance`/命令时长驱动时间，或本身运行在 wall 时钟） |
| issue 4 的漂移在 `wall` 下可复现、`sleep` 下不可 | ✅ 同上：`wall` + 忙等 50 ms 复现 1508 Ω 一类的偏大读数（> 1520），`sleep` 恒为 1500 |
| 多测试台规则、serve 降级、记录与回放 | ✅ 两个 `sleep` 测试台同时推进、`wall` 测试台不受影响、关闭后恢复真实 `time.sleep`；serve 把 `sleep` 按 `off`（`test_serve_treats_sleep_as_off`）；记录头部 `idle` 与 `reason: sleep` 事件、回放通过（`test_sleep_record_names_idle_and_replays`） |
| `scripts/check.sh` 通过，schemas 最新 | ✅（提交前运行） |

## 插件出口：入口点组件与工作区组件（Issue 47）

| 验收标准 | 自检 |
| --- | --- |
| 入口点插件可被定义使用：`simubench.components` 入口点默认加载，组件与内置同接口（`plugin_api: 1`，不匹配的模块拒绝并撤销注册） | ✅ `tests/test_plugins.py`（fake entry point 加载后测试台可搭建；`plugin_api = 2` 被拒） |
| 未开启时工作区插件不被导入（导入即失败的文件不会执行）；开启后可用 | ✅ 同文件：`bomb.py` 默认零导入，`--allow-plugins` / 工作区设置 `.simubench/settings.json` 后执行并可用；`test_security.py::test_workspace_plugins_are_opt_in` |
| 回放时插件哈希变化给出警告 | ✅ `test_replay_warns_when_a_plugin_changed`（改文件后告警）；记录了插件而回放未加载也告警（`…_not_loaded`） |
| 运行记录头部带插件文件哈希（D36）；doctor 与诊断包列出插件及来源（不含源码） | ✅ 记录头部 `plugins`（来源、origin、sha256）；`doctor` 增加 plugins / workspace plugins 两项检查；清单进 `version --full` 与诊断包的 `manifest.json` |
| 启动时打印加载了哪些插件；MCP 不提供写 `.py` 的工具 | ✅ serve / mcp 启动横幅打印（含"present but not enabled"提示）；文件写入只覆盖测试台与定义 yaml（`docs/security.md`） |
| `docs/security.md` 更新；`scripts/check.sh` 通过 | ✅ 新增"工作区插件代码"一行与"不在防护范围内"两条；check.sh 全绿（web 经 node_modules 本地二进制复核） |

## 计量类仪表的通用能力（metrology-authoring.md，issue 86）

| 完成条件 | 自检 |
| --- | --- |
| 型号不依赖专用组件的检查、写型号的操作手册、覆盖表草稿 | ✅ issue 93：组件的 `generic` 标记（组件目录与 MCP `list_components` 显示），`tests/test_model_components.py`（两台 Siglent 的 4 处使用为例外，清单只能缩短；新增使用与已还清未删都会失败并指明定义与组件）；`.claude/skills/real-model/SKILL.md`（其中的命令逐条运行过）；`simubench coverage <id> --draft`（新建、补充时保留手写内容、分节，`tests/test_coverage_draft.py`）；`.github/ISSUE_TEMPLATE/real-model.md` |
| 测量核心：量程、超量程、分辨率、输入阻抗、按量程的误差 | ✅ issue 87：手动量程取能容纳设定值的最小档、超限取最大档；自动量程按真值、滞回带 `[down, up] × 满量程`，判定发生在测量命令；超量程读 `overload_value`；分辨率（含 NPLC 的 `table` 映射）量化读数；隐藏误差可按（功能、量程）给表；输入阻抗由核心自己 stamp 进电路（量程或 `high_z` 变化触发重解），`tests/test_meter_core.py` 覆盖分压电路两种 `high_z` 取值、与手工计算差小于 1 ppm；`sb-dmm-1` 现有测试不改通过；#88 的最小 `sample()` 读到的也是量程系统的读数（metrology-authoring.md 2.1） |
| 触发模型、读数存储、待完成操作、`*TRG` | ✅ issue 88：只用通用组件写成的测试万用表（`tests/definitions/test-trigger-meter.yaml`）上，`TRIG:COUN 3; SAMP:COUN 2; INIT; FETC?` 给出 6 个读数，虚拟时间为 6 × 读数时间 + 3 × 延时；`*OPC?`、`*WAI`、`*OPC` 等待运行完成，`*CLS` 取消；总线触发、`READ?` 的死锁、运行中 `INIT`、不在等待时的 `*TRG` 的错误；`R?` 的块格式与取出、`DATA:POINts?`、`DATA:REMove?`；命名缓冲区与按元素读取；含触发的运行记录回放一致（`tests/test_trigger_model.py`，10 项）；示波器的 `*OPC` 改用同一机制，现有用例与验收不改通过 |
| 校准存储 | ✅ issue 89：新组件 `calibration_store`（键 = 功能与实际量程，`$状态` 或 `组件.参数` 条目；安全码、参考值、调整的容差与回复参数化；台账计数/日期/时间/字符串），`dmm_core` 设 `store` 时四个 `cal_*` op 全部委托、读数按键取系数，未设时保留原实现；测试定义两量程各带不同隐藏增益误差，分别调整后 as-left 均小于 1 ppm 且互不影响，DC 调整不进 AC 的键；安全码错误、未解锁调整、输入偏离参考、`COUNt` 不可设各有用例；`STORe` 后计数/日期/时间正确，`*RST` 保留台账与已存系数、丢弃未存储调整（`volatile_until_store: true`）；`sb-dmm-1` 定义改造（台账命令改 op、系数状态删除）但回复逐条不变，`tests/test_calibration.py` 不改通过；`tests/test_calstore.py` 7 项；实现与设计的差别见 metrology-authoring.md 4.1 |
| 校准源、按单位分派的参数、错误查询的声明 | ✅ issue 90：新组件 `calibrator_output`（role: driver。待机端口不进电路；工作为带 `z_out` 的电压源——`z_out` 为 0 时理想源带电流跟踪，ACV 为单根谱线；过载按直流工作点判断（D34 同一迭代）使 operate 状态回到待机并按 `overload_error` 参数报错，OPER 重新工作；设定改变或进入工作后幅度按一阶过渡（τ = settle/5），完成时刻登记为待完成操作，`*OPC?`/`*WAI` 等待并把虚拟时间推进 settle，settle 可按功能给表、为 0 时即时；`standby_above`：工作中新旧电平任一超过阈值时改设定即回待机，需再次 OPER；隐藏误差 `gain_error_ppm`/`offset` 作为真值来源）；`ParamSpec` 新类型 `quantities`（`OUT 1 V, 1 kHz` 按单位写入对应状态，`sets`/`combine` 可同时写功能，`if_zero` 判 0 Hz 直流；倍率按 SCPI（mV、kHz、MHZ/MOHM 兆），组合键按 units 表声明顺序规范化、输入顺序无关；缺单位/未知单位/重复单位/组合不在 combine 表的错误码是 ParamSpec 参数，文字按定义 `errors`；`combine` 声明后不在表中的组合报错；查询用 reply 模板组织）；`system` 组件 `next_error`/`error_count` op（非 SCPI 树的错误查询如 Fluke `ERR?`，回复模板 `{code},{text}`，code 按 `format.error_code` 带符号、text 按定义 `errors`，空队列给 0）；静态校验检查 units/sets/combine 的状态与枚举值、组合键单位、`if_zero` 状态（`E-DEF-QUANTITY` 等），quantities 命令的形状由 schema 拒绝；测试定义 `tests/definitions/test-calibrator.yaml`（非 5522A，数值随意），`tests/test_calibrator.py` 27 项（OPER 后读到设定值、STBY 后 0 V、`OUT 1 V, 1 kHz` 交流 1 V、0 Hz 直流、`*OPC?` 恰推进 settle、建立中途读数介于新旧之间、过载跳回待机并报错、再 OPER 再跳、`standby_above` 新旧两侧、z_out 分压、隐藏误差真值、quantities 组合/倍率/反例、ERR? 与 ERR:COUNt?、静态校验反例）；`tests/definitions/test-trigger-meter.yaml` 增加 `sense.function` 状态与 `:MEASure` 命令（现有测试不改通过）；schemas 与前端类型重新生成（instrument.schema.json 与 openapi.ts 有 diff，已提交），`scripts/check.sh` 全绿；实现与设计的差别见 metrology-authoring.md 5.4 |
| 四线测量与引线电阻 | ✅ issue 91：配件 `lead`（`IN`/`OUT`，参数 `r`，`role` 为空——端接判断不穿过它，与同轴线缆有对照用例）；`smu_core` 增加 `lo`、`sense_hi`、`sense_lo` 端口与 `floating`、`remote_sense` 参数（`sb-smu-1` 状态 `terminal.floating` / `sense.remote`，命令 `:SYSTem:FLOATing` / `[:SENSe]:REMote`），电压环经新求解元素 `loop_source` 调节 sense 对（修正节点分析一个受控约束，电流仍走 force 端，引线压降精确），读数与限值切换（D34）都按所调的电压判断；`floating: false` 时单端源逐位等价于原实现，`sb-smu-1` 全部测试与 SMU 验收场景不改通过；sense 悬空给 `W-SENSE-UNCONNECTED` 警告、运行时读数报 -221，两端同网络给 `E-SENSE-SHORT`；`lead` 在画布按电阻值显示（`0.5 Ω`）；实现与设计的差别见 metrology-authoring.md 6.1。`tests/test_four_wire.py` 15 项（二线 11 V / 四线 10 V 与手工计算一致、限值 10.5 V 在四线下不切换二线切换、浮地 ±5 V 对分、悬空回退）、`acceptance/test_four_wire_scenario.py` 3 项（bench `smu_four_wire.bench.yaml`：0.5 Ω×2 引线 + 10 Ω + 1 A）；schemas 重新生成无变化，`scripts/check.sh` 全绿 |
| `smu_core` 接入触发模型与缓冲区、源与测量量程 | ⏳ issue 94 |
| 四线测量与引线电阻 | ⏳ issue 91 |
| `smu_core` 接入触发模型与缓冲区、源与测量量程 | ✅ issue 94：`smu_core` 实现 sampler 协议（读数时间 = NPLC 规则 + 源延迟；扫描的每点经 `sample()` 取读数，`run_samples()` 声明运行大小，`run_begin`/`run_done` 保存并恢复被扫描驱动的源设置，`sample_fields` 给出 VOLT/CURR/RES/TIME/STAT），自有缓冲区删除，`sb-smu-1` 改用 `trigger_model` + `reading_buffer`；金标准测试（34 条消息）回复与虚拟时间逐条不变，仅 `INIT` 即返、`*OPC?`/`FETCh?` 等待（设计 3.1，`tests/test_smu_migration.py`），SMU 验收 16 项不改通过（步进时钟下即时运行当场完成）；量程规则收拢到 `library/components/ranging.py` 与 #87 共用：源包络 = `source_ranges` + `range_limits` 默认表（21 V / 105 mA 为 210 V 档封顶一项，`sb-smu-1` 无需新参数），手动/自动源量程改包络；测量量程（按功能）、手动 `measure_range_voltage/current`、自动量程滞回、超量程读 `overload_value`（`RES` 取原始值）；触发模型新参数 `digits`、`fetch_from_buffer`，`fetch`/`read` 按命令参数 `elements` 出多元素回复，`measure` 仍把那一条读数写入存储（替换内容）；合并后 main 的 e2e 暴露两处收尾：SMU 屏（含通用触发模型）的 readings 面板会先完成已到期的待完成操作（修 `TriggerModel.readings` 属性与 `Component.readings()` 方法同名导致的 `'list' object is not callable`），执行键改发 `:INITiate;*WAI`；新测试定义 `tests/definitions/test-smu-trigger.yaml`（计数 × 采样的个数与时间、命名缓冲区、按元素读取、测量量程与自动量程、源包络与手动源量程，`tests/test_smu_trigger.py` 7 项）；实现与设计的差别见 metrology-authoring.md 3.6 |
| 个体偏差按指标抽取 | ✅ issue 34：同一测试台同一种子抽到相同的值，换种子不同，200 个种子全部在范围内（`sb-dmm-1` 两项、`sb-awg-2` 每通道一项、DUT 电阻）；与测试台中其他仪表无关；写死的值优先；读数等于按抽取的误差手工计算的值；按表抽取；记录头部与回放校验；静态校验；编辑操作；会话接口在 `reveal_true_values` 关闭时不给出值；#87 之后按量程抽取的增益误差进入每个量程的读数（`tests/test_deviation.py`，12 项）；检查器“个体偏差”与“按指标抽取”开关（端到端测试）；现有测试台行为不变（全部现有测试不改通过） |
| 定标判定的真值核对 | ✅ issue 92：不 import SimuBench 的定标脚本（设参考点、读被校表、按守卫带规则判定）写出的报告，`simubench verdicts` 逐点给出 `false_accept` / `withheld` / `correct`，真误差（读数 − 真输入）与手工计算一致到纳伏；报告行按顺序对上运行记录中被校仪表的数字读数回复（按回复的位数比较），对不上的行、映射外的决策值、颠倒的限值、缺失的列都指明行号报出，不猜；`instrument` 可以是测试台实例名或报告的列名；多余的数字回复警告；退出码：有误判 1、输入对不上 2；MCP `check_verdicts` 返回同样的结果、与 `read_true_values` 一样需 `--allow-control`；运行时保密规则不变（`docs/security.md`）；`tests/test_verdicts.py` 13 项（MCP 协议级含内） |
| Keysight 34465A（只写 YAML） | ✅ issue 7：定义 `src/simubench/library/instruments/keysight-34465a.yaml`（reference = Truevolt 系列 Operating and Service Guide 34460-90901, Edition 13, 2025-09；specs 记数据表 5991-1983EN 的直流/交流精度；deviations 按 1 年、10 PLC 的直流精度抽增益与零点），覆盖表 233 条（实现 56 · 接受 10 · 未实现 167），手册用例 `tests/models/keysight-34465a/`（26 条）与输入阻抗对照用例 `tests/models/keysight-34465a-impedance/`（2 条，跑在示例测试台 `examples/benches/keysight-34465a.bench.yaml` 上：SDG2042X 经 900 kΩ/100 kΩ 分压，10 MΩ 与 >10 GΩ 读数不同且与手工计算一致，含求解器每节点 1e-12 S 的 GMIN 与 SDG 的 50 Ω 内阻）；PyVISA 脚本 `examples/scripts/keysight_34465a_measure.py`（CONFigure、BUS 触发、多次 `*TRG`、`R?` 取回）按真机写法、不改一行在测试中运行（`tests/test_keysight_34465a.py`，7 项：脚本、阻抗与 ACV 手工计算、校准 as-found/as-left < 1 ppm、按量程调整、NPLC 计入虚拟时间、偏差抽取）；校准用 `calibration_store`，键 =（功能， 实际量程），出厂安全码 AT3446XA；`src/simubench/library/components/` 无改动（#93 检查通过）。与手册的已知偏差逐条写进覆盖表说明（CONFigure 换功能需 FUNCtion——平台缺"命令头写常量"，读数时间单一映射、AC 沿用 DC 表，TRIG:DEL 在每触发前而非每测量间，校准错误码 701/702 对应手册 702/703/708 等）。`scripts/check.sh` 全绿 |
| Fluke 5522A（只写 YAML） | ✅ issue 8：定义 `src/simubench/library/instruments/fluke-5522a.yaml`（reference = Fluke 5522A Multi-Product Calibrator Operators Manual，PN 3795084, January 2011，从 fluke.com 下载的 PDF 核对；`*IDN?` 格式 `FLUKE,5522A,<序列号>,<主 CPU+前面板 CPU+inguard PGA 固件>`；builtins 限定为手册的公共命令——:SYSTem:* 一类 SCPI 头不是 5522A 的命令，错误队列用 `ERR?`/`FAULT?`（`system.next_error`，回复 `{code},"{text}"`，空队列 `0,"No Error"`），日期时间用 CLOCK）；输出经 `calibrator_output`：`OUT 10 V` 直流、`OUT 1 V, 1 kHz` 交流、`OUT 60 HZ` 只更新频率（quantities：units V/HZ，combine "V"→DCV、"V,HZ"→if_zero、"HZ"→空更新；缺单位报手册的 519、未知单位 1305、同单位两次（双输出未建模）506）；量程 ±1020 V / 500 kHz（超限报 -222，SCPI 码——手册的 1300 系列错误码无法由状态范围检查给出）；待机上电与 *RST、待机输出端断开进电路求解、z_out 5 mΩ、i_max 5 mA（过载跳回待机报手册的 1503 "Output current lim exceeded"）、settle 5 s（手册 <5 s，`*OPC?`/`*WAI` 等待并计入虚拟时间）、`standby_above` 33 V（≥33 V 改设定回待机需再 OPER，手册第 3/4 章）；隐藏 gain/offset 作真值，deviations 取 1 年指标最紧档（增益 20 ppm、零点 1 µV，标量——calibrator_output 无按量程表，DC/AC 两套限值表达不了）；1 年精度规格表记 specs。覆盖表 100 条（实现 16 · 接受 5 · 未实现 79），手册用例 `tests/models/fluke-5522a/cases.yaml` 27 条（跑在带 sb-dmm-1 负载的 bench 上，一致性检查通过）；示例台 `examples/benches/fluke-5522a.bench.yaml`（5522A → SDS2104X Plus：直流 10 V 与 1 kHz 交流 1 V 在示波器可见，含 8-bit ADC 量化），精度台 `validation/benches/fluke-5522a-dmm.bench.yaml`（deviation: from_specs：OPER 后读设定值、误差在 ±20 ppm + 1 µV 内且随种子可复现，STBY 后 0 V）；`tests/test_fluke_5522a.py` 12 项。与手册的已知偏差逐条写进覆盖表说明：`OPER?` 查询形式与 `LOCAL`/`REMOTE`/`LOCKOUT` 的状态保存受无参数命令声明限制（核心 issue 119，`OPER?` 报 -113、LOCAL 等被接受不保存）、`OUT` 裸数值形式（沿用当前单位）不建模、`ERR?` 队列溢出按 SCPI-99 报 -350（手册为仪器码 1）、正错误码在 *ESR? 记为 DDE 位（手册的 CME/EXE 类）、NORMAL LO 是机内地不单独成端口、EARTH/EXTGUARD 只接受不影响仿真。`src/simubench/library/components/` 无改动（#93 检查通过）；`scripts/check.sh` 全绿 |
| 衡量：34465A、5522A、2450 只写定义（issue 7、8、12），定标演示的核对不写专用代码（issue 9） | ⏳（issue 7、8 ✅；12、9 待做） |

## DUT 是角色而不是一类对象（Issue 100，D8）

| 验收标准 | 自检 |
| --- | --- |
| `bench/1` 文件（`duts`、`add_dut`、`set_params dut:`）读取时迁移，行为不变 | ✅ 迁移在测试台模型中进行，文件、接口、MCP 与测试中的旧数据都经过它；原来的每个 DUT 成为标为 DUT 的元件（`tests/test_migrate.py`）；全部现有测试只改了被改名的字段即通过；`add_dut` 与 `dut:` 字段名仍被接受（`tests/test_dut_role.py`） |
| 仪表可以标为 DUT；会话接口、MCP 与界面显示，编辑操作设置与取消 | ✅ `set_dut`；会话的 `parts` 与 `duts`（名称）、仪表与元件的 `dut`；MCP `describe_session`；端到端测试：在搭建模式把示波器标为 DUT、取消 RC 网络的标记，画布与资源树的标记随之变化 |
| 界面：资源树分组、元件库命名、画布标记、检查器开关 | ✅ 资源树“仪表 / 元件”，元件库“元件”，节点与资源树的 DUT 徽标，检查器的 DUT 开关；视觉基线不变（22 项通过） |
| 仓库中的测试台改写为 `bench/2` | ✅ 示例、验证与验收测试台；`rc_rc_buffer` 的缓冲器与 `smu_reverse` 的电源不再标为 DUT，`lowfreq_cal` 不标 DUT |

## 搭建模式下说明画布与仪表屏幕各显示什么（Issue 103）

| 验收标准 | 自检 |
| --- | --- |
| 复现报告的场景：搭建模式下经 SCPI 打开 CH1 输出，画布显示“关”并说明是初始设置；屏幕横幅列出运行中值与初始值 | ✅ 画布左上角的“初始设置”说明；搭建模式下打开的仪表屏幕顶部有“运行中的值”横幅，逐项列出与测试台文件不同的设置（“CH1 输出：开（初始：关）”）；端到端测试 |
| 操作模式下不显示这两处说明 | ✅ 同一端到端测试 |
| 视觉基线 | ✅ 不变（说明只出现在搭建模式，Storybook 故事中没有） |

## 脚本连着时，界面不打乱仪表状态：远程状态与 LOCAL（Issue 121，D25、D32）

| 验收标准 | 自检 |
| --- | --- |
| 端口上有连接时 `configure`、`scpi`、`panel` 返回 409 并列出客户端；LOCAL 后可操作；远程客户端再发命令后恢复远程 | ✅ `tests/test_remote_state.py`：被拒时脚本的设置不变、另一台没有连接的仪表照常可操作；LOCAL 与回到远程各记一个 `remote` 事件（来源：界面 / 脚本的客户端） |
| `reset`、`advance`、打开测试台（重建）、关闭：有连接时不带 `force` 返回 409，带 `force` 照常执行；没有连接时行为不变 | ✅ 同一测试文件；现有测试不改通过（768 项） |
| SOCKET 脚本与界面控制台交错查询时各自拿到自己的回复 | ✅ `Session.exchange` 把一条消息的写入与回复读取放在一次动作内（SOCKET 服务器与界面控制台都用它）；`test_exchange_is_one_action` 在脚本写入后立刻插入界面查询，改动前脚本的回复被界面取走（测试失败），改动后各得其所 |
| 端到端：仪表面板的远程标记与 LOCAL；有连接时切回操作模式的确认与“稍后重建” | ✅ `web/e2e/remote.spec.ts`（2 项）：检查器的“远程控制中”横幅、控制台被拒的提示、LOCAL 后命令可用、脚本下一条命令后回到远程；切回操作的确认框列出脚本，“稍后重建”保留连接，顶栏“测试台文件已修改”再重建。确认框每次提问是新实例（重开时旧实例的内容未卸载会被新遮罩盖住，重复运行 24 次验证） |
| D25、D32 按新行为修改；`scripts/check.sh` 全绿 | ✅ |

## 组件粒度：示波器（instrument-authoring.md 第 8 节，issue 123）

| 完成条件 | 自检 |
| --- | --- |
| G1 结构拆分：时基、触发、ADC 组、每通道的垂直与探头模块；采集引用它们；宏在加载时展开（issue 133） | ✅ 新组件 `scope_vertical`（旧名 `scope_frontend`）、`scope_probe`、`scope_adc`、`scope_timebase`、`scope_trigger`；`scope_acquisition` 按名称引用，`E-DEF-COMPONENT-REF` 检查引用（缺失、拼错、类型不对各有反例）；宏在 `InstrumentDefinition` 构造时展开，`sb-dso-4` 的一个组件写法与显式写法的定义哈希相同，重复展开不变、不改动调用方的数据，无法展开时报 `E-DEF-MACRO`（`tests/test_scope_modules.py`，6 项）；刻度与偏置范围按输入端检查，超出报 -222 并保留旧值，改探头比不检查；`sb-dso-4`、`sb-dso-2x` 显式写出模块，SDS2104X Plus 与低频校准参考示波器经宏展开。行为不变：SDS 的手册用例、`tests/test_siglent_sds.py`、示波器验收与低频校准测试不改通过；只改了检查内部结构的断言（参数所在的模块、经探头模块的连线、内部结构图的组件与连线数、展开后诊断的指针），并在内部结构图中加入新模块的中英文名称。Python 774 项（含验收 115 项）、端到端全部通过 |
| G2 采集的同步语义：采集作为待完成操作，同步规则按型号声明，命令耗时（issue 134） | ✅ `completion`、`opc_waits`、`while_waiting`、`no_trigger`；SDS2104X Plus 声明在虚拟时间中完成，覆盖表注明 `*OPC?` 是否等待采集未经核实。`tests/test_scope_sync.py`（9 项）：单次后立即读得到上一帧且采集次数不变；状态随虚拟时间依次为 Ready、Trig'd、Stop；`opc_waits: false` 时 `*OPC?` 立即回复；`no_trigger: return`；`while_waiting: error` 报 -221；停止结束等待；`:DIGitize` 推进到记录填满；定义声明 `*RST` 耗时 0.5 s；没有触发时 PyVISA 读 `*OPC?` 超时。依赖旧行为的用例按新语义改正（SDS 手册用例 1 条、SDG 测量、噪声验收、等待中的单次采集测试），D32 相应修改；面板按下的单次当场完成，端到端测试中“运行后的单次”采集次数按新语义为 2。Python 783 项（含验收）与端到端 38 项全部通过 |

