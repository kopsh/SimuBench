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
| Keysight 53220A（D42，issue 200） | ✅ 手册用例 20 条；覆盖表 198 条（实现 45、接受 9、未实现 144）；频率/周期（`MEASure?` 与 `CONFigure`+`READ?`，`(@1)`/`(@2)` 路由到对应输入）、门控累加、两通道时间间隔、输入耦合/阻抗/阈值、内部/外部时基、统计（均值/最小/最大/标准差/点数/清空）；`SAMPle:COUNt`×`TRIGger:COUNt` 与总线触发；示例测试台 `examples/benches/keysight_53220a.bench.yaml`，真机风格脚本 `examples/scripts/keysight_53220a_measure.py` 在 `tests/test_keysight_53220a.py` 中运行；`src/simubench/library/components/` 未改动。未建模：53230A 的无间隙连续测量/时间戳/突发与脉冲测量、读数存储器与文件系统、相位/脉宽/占空比/上升下降时间、外部门控与高级门控、状态寄存器组。组件限制：`counter_core`/`counter_statistics` 的操作应答由组件写成 13/11 位有效数字，不随定义的 15 位格式变化（记入缺口清单）；统计不在每次 `INITiate` 时自动清空，脚本在取统计前按手册用 `CALCulate:AVERage:CLEar` 清零 |
| Keysight E36312A 三路可编程电源（D42，issue 198） | ✅ 定义 `src/simubench/library/instruments/keysight-e36312a.yaml`（`reference` = E36300 Series Programmable DC Power Supplies Programming Guide，E36311-90008，Edition 12，June 2025），覆盖表 122 条（实现 21、接受 15、未实现 86）；手册用例 `tests/models/keysight-e36312a/cases.yaml` 20 条，逐条带命令章节；三路独立输出（CH1 6.18 V/5.15 A、CH2/CH3 25.75 V/1.03 A，CH3 按 E36312A 正常模式为正电压）、通道列表的 VOLT/CURR/OUTP/MEAS、OVP 的设定/查询/清除与 729 开输出拒绝、四线检测（INT/EXT）、显示/蜂鸣/LXI/远程本地等接受命令；示例测试台 `examples/benches/keysight_e36312a.bench.yaml`（CH1 10 Ω 含 SENSE，CH2/CH3 各 100 Ω），真机写法脚本 `examples/scripts/keysight_e36312a_check.py` 在 `tests/test_keysight_e36312a.py` 中不改一行运行（7 项：脚本、继电器虚拟时间、四线检测、CC 限值、三路独立与通道列表、越限、OVP 跳闸与清除）；`src/simubench/library/components/` 未改动；`simubench validate` 对定义无诊断。未建模：组合/跟踪（#259：B2 组件仍是 E36311A 的 ±25 V 拓扑）、OCP 与 CV/CC 状态寄存器（#271）、列表/触发/数据记录/校准/数字口、INSTrument:SELect（省略通道列表的裸命令固定按通道 1）、实时时钟与身份切换。偏差：手册的 MEASure 只有电压/电流，没有功率查询，脚本按 V×I 计算；回读由 `supply_readback` 写成 7 位尾数，定义里 8 位尾数的格式适用于设定值；`VOLT:PROT:TRIP?` 的裸查询按通道 1（选中输出语义未建模）。`scripts/check.sh` 全绿 |
| 内置仪表只有真实型号，`sb-*` 不发布（issue 35） | ✅ `sb-*` 定义移到 `validation/instruments/`，演示台移到 `validation/benches/`；定义搜索路径 `SIMUBENCH_DEFINITIONS`，测试经 `conftest.py` 加载；服务启动会话与回放运行记录也搜索工作区的 `instruments/`（此前只有校验搜索）；`tests/test_release.py` 与 CI 打包任务检查内置定义、`examples/` 与 wheel 中没有 `sb-*` |
| Keysight B2901A 精密源表（issue 193，只写 YAML） | ✅ 定义 `src/simubench/library/instruments/keysight-b2901a.yaml`（`reference` = B2900 Series SCPI Command Reference B2910-90030, Edition 2, July 2011；User's Guide B2910-90010 Edition 6 用于前面板与量程说明），覆盖表 307 条（实现 77 · 接受 12 · 未实现 218）；手册用例 `tests/models/keysight-b2901a/cases.yaml` 19 条，逐条带 Command Reference 章节；示例测试台 `examples/benches/keysight_b2901a.bench.yaml`（1 kΩ DUT）与真机写法脚本 `examples/scripts/keysight_b2901a_sweep.py`（MEASure 单点 + 线性阶梯扫描 + FETC:ARR? 读测量数组）在 `tests/test_keysight_b2901a.py` 中不改一行运行（4 项：脚本、合规转 CC、电流源、NPLC 与扫描虚拟时间）；`src/simubench/library/components/` 无改动（`tests/test_model_components.py` 通过），`simubench validate` 无诊断。已知偏差写在覆盖表说明：源量程 V/I 共用一组设置（按当前源功能解释），FETCh/READ/TRACe 固定给出 VOLT,CURR,RES,TIME,STAT（元素选择、SOUR 元素、标量“最新一条”未实现），STAT 元素按数字格式回复 |
| SRS SR830 锁相放大器（issue 195，只写 YAML） | ✅ 定义 `src/simubench/library/instruments/srs-sr830.yaml`（`reference` = *SR830 DSP Lock-In Amplifier Manual*，Revision 2.5（10/2011），firmware 6-1；第 5 章 Computer Interface，默认值取第 4 章设置表），`dialect` 为 SRS 行式（4 字母助记符、裸数值回复），覆盖表 93 条（实现 21 · 接受 6 · 未实现 66），按手册第 1 章命令列表与第 5 章逐条；手册用例 `tests/models/srs-sr830/cases.yaml` 9 条（每条带 5-x 节），覆盖全部实现与接受命令；示例测试台 `examples/benches/srs_sr830.bench.yaml`（SR830 SINE_OUT → 1 kΩ/100 nF RC → SIG_IN，AUX_OUT2 → AUX_IN2，`idle: sleep`）与真机写法脚本 `examples/scripts/srs_sr830_rc_sweep.py`（`GPIB0::8::INSTR`、`time.sleep` 等滤波建立、R/θ 六点频率扫描、AUX 回读）在 `tests/test_srs_sr830.py` 中不改一行运行（4 项：脚本与电路闭式解逐点对照、GPIB 地址 8 的映射、X/Y/R/θ 同一相量、受限取值与未实现命令被拒绝并记录）；`src/simubench/library/components/` 无改动（`tests/test_model_components.py`、`test_block_coverage.py` 通过），`simubench validate` 无诊断。SR830 只有 GPIB/RS-232（无 LAN）：测试台把 `GPIB0::8::INSTR` 声明为实例 `address:`，进程内 PyVISA 后端按该地址映射，脚本不改。被能力 issue **#273**（#181 下）阻塞并记 `not_modeled` 的组：数据缓冲（SRAT/SEND/TRIG/TSTR/STRT/PAUS/REST/SPTS?/TRCA?/TRCB?/TRCL?/FAST/STRD）、状态字节（*ESE/*ESR?/*SRE/*STB?/*PSC/ERRE/ERRS?/LIAE/LIAS?）、按索引查询与偏置/扩展（DDEF/FPOP/OEXP）、输入方式（ISRC 只声明 A、RSLP 只声明正弦过零、IGND 只保存回读）。已知偏差写在覆盖表说明：检测频率上限 500 kHz 而非 102 kHz 且 HARM 不钳位、RMOD 储备按固定 40/20/0 dB、PHAS 不回绕、SLVL/AUXV 不取整、FREQ 在外部参考下也接受、OUTP?/OUTR?/OAUX? 用引擎工程记数、未实现命令按平台规则记 -113 事件（SR830 无错误队列，查询不回复） |
| Keysight N9320B 频谱分析仪（issue 196，只写 YAML） | ✅ 定义 `src/simubench/library/instruments/keysight-n9320b.yaml`（`reference` = N9320B RF Spectrum Analyzer Programmer's Guide N9320-90029, Edition 5, December 2020），覆盖表 261 条（实现 45 · 接受 19 · 未实现 197）；手册用例 `tests/models/keysight-n9320b/cases.yaml` 11 条，逐条带手册章节；示例测试台 `examples/benches/keysight_n9320b.bench.yaml`（SDG2042X 10 MHz、2 Vpp 接 RF_IN）与真机写法脚本 `examples/scripts/keysight_n9320b_measure.py`（复位、频率/带宽/扫描自动耦合、标记峰值搜索、频率计数器、迹线数据、错误队列）在 `tests/test_keysight_n9320b.py` 中不改一行运行（5 项：脚本、自动耦合、标记、迹线确定性与单次扫描虚拟时间）；`src/simubench/library/components/` 无改动（`tests/test_model_components.py` 通过），`simubench validate` 无诊断。未建模：标记组（差值/固定参考标记、标记开关、最小与左右峰值搜索、峰值搜索方式、计数器开关）依赖能力 issue #274，覆盖表标 `not_modeled`；触发、测量应用、限制线、迹线 2–4 / MINHold / VIEW / BLANK、NEGative/NORMAL/自动检波、零跨度、FFT 扫描、平均类型、预放、文件存储与状态寄存器组未建模。已知偏差：组件操作应答（标记 X/Y、计数器、迹线）由组件写成 7 位有效数字，不随定义的 16 位格式；自动衰减按组件规则（参考电平 0 dBm 时 10 dB）与手册 `*RST` 20 dB 不同，自动扫描时间也与手册 `*RST` 100.2 ms 不同；小中心频率配大跨度时窗口按当前跨度滑动，手册“先设中心、后设跨度”的写法要先设跨度。 |
| 型号：Keysight 34465A、Fluke 5522A、Keithley 2450 | ⏳ 各自的 issue |

## 定标演示：Fluke 5522A 校准 34465A（Issue 9）

| 完成条件 | 自检 |
| --- | --- |
| 测试台与台账 | ✅ `examples/demo/calibration.bench.yaml`：5522A OUT 接 34465A IN；万用表带按量程的隐藏增益与零点误差（DC 各档 +180…+400 ppm、交流 +350 ppm、零点 +300 µV）；`assets` 登记两台仪表，万用表校准即将到期（运行开始日 2026-05-04，due_in_days = 16）；`run` 固定日期、命令时钟、`idle: off` |
| 定标脚本只用标准命令与 PyVISA | ✅ `examples/demo/calibrate_34465a.py` 不 import SimuBench；读 `*IDN?`、查台账、as-found（0、±1、±10、100 V；1 年指标 + 20 % 保护带）、解锁后逐量程调零/调增益并 `CAL:STOR`、as-left；报告 CSV 含每点的相位、设定值、读数、限值与判定；交流点单独一次运行（列映射一次一种功能） |
| 调整命令按真机语义 | ✅ `:CALibration[:ALL]` 允许 set 形式：set 调操作且不应答，query 返回结果（引擎通用修正：操作命令的 set 形式不再把操作的返回值发到线上）；演示用 `CAL` + `:SYSTem:ERRor?`，不产生额外的数字回复，`simubench verdicts` 的读数序列不被校准状态打乱 |
| 真值核对 | ✅ `simubench verdicts` 用 `verdict-dc.yaml` / `verdict-ac.yaml`：DC 12/12 correct、AC 2/2 correct，无 false_accept/false_reject/withheld；真值含参考源 5 mΩ 与万用表 10 MΩ 的分压 |
| 故障演示 | ✅ `examples/demo/calibration-fault.bench.yaml`：参考源第 2 s 断开 30 s，脚本得到 `VI_ERROR_CONN_LOST`，不写报告；台账过期/停用时脚本拒绝运行（测试覆盖） |
| 讲解文档 | ✅ `docs/demo-calibration.md`：准备、运行、真值核对、故障、界面观察、讲解要点、已知限制 |
| 自动测试 | ✅ `acceptance/test_calibration_demo.py` 4 项：DC as-found 6 点全超差、as-left 全合格、verdicts 判定正确；AC 两点在限值内；断开时脚本报错；台账过期时拒绝 |
| 检查 | ✅ `uv run simubench validate` 无诊断；`scripts/check.sh` 全绿 |

## 覆盖表的“接受”只用于不影响数据的命令（Issue 132，D42）

| 完成条件 | 自检 |
| --- | --- |
| 覆盖表格式与一致性检查 | ✅ `CoverageEntry.affects_data`：只在“接受”上要求为 `false`，缺少它或写为 `true` 由 `check()` 报错（`tests/test_real_models.py` 反例）；`docs/real-models.md` 第 2、6 节写明规则；SDS、SDG、34465A、DSO-X、5522A 与虚构样例 acme-dm1 的“接受”条目全部标注 |
| SDS2104X Plus 的 7 条命令 | ✅ `:CHANnel<ch>:SKEW` 已在 G3 实现（数字延迟进入记录，手册用例）；`:TIMebase:REFerence` / `:TIMebase:REFerence:POSition` 实现：`scope_timebase` 新增 `reference_mode` / `reference_position`，POSition 时按百分比决定触发在记录中的位置，DELay 时仍由延迟移动（`tests/test_siglent_sds.py` 用记录的触发序号与原点验证）；`:ACQuire:MMANagement`、`:CHANnel:REFerence`、`:TRIGger:EDGE:COUPling` 改为未实现（-113，覆盖表写明原因，手册用例去掉这些步骤）；`:ACQuire:MODE` 只保留 YT，XY / ROLL 报 -224；`:ACQuire:TYPE` 的 PEAK / ERES 已由 D46 实现，覆盖表注明 |
| 其他真实型号中影响数据的“接受”条目 | ✅ 34465A 的 `FORMat[:DATA]` 只接受 ASCii，`REAL` 报 -224（不再静默按 ASCII 返回）；`FORMat:BORDer` 只影响未实现的二进制传输，保持“接受”并标注；其余（显示、蜂鸣器、按键音、LOCAL / REMOTE / LOCKOUT、EARTH / EXTGUARD、SYNC、外部触发沿）逐条核对，均不影响数据 |
| 手册用例与测试 | ✅ 34465A 的格式用例改为 REAL 报错、ASC 回读；SDS 用例去掉未实现命令并保留 MODE YT；新增 `tests/test_siglent_sds.py` 两项（参考位置改变触发序号与原点、未实现命令 -113、MODE YT / TYPE PEAK）；`scripts/check.sh` 全绿 |

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

## 仪表验收页（instrument-authoring.md 7.2，issue 129）

| 完成条件 | 自检 |
| --- | --- |
| 概览：身份与 `*IDN?`、类别、端口与介质、定义依据的版本；功能与量程由定义的面板（类别默认布局或声明）整理；技术指标与个体偏差限值并排显示 | ✅ 34465A 显示直流/交流读数、100 mV–1000 V 与 AUTO、34460-90901；没有面板的类别（示波器等）退回该定义的设置表；页面注明面板来自声明还是类别默认 |
| 命令：按命令树列出命令头、参数（类型、单位、范围、可选）、可设置/可查询、绑定、回复模板与说明；真实型号另显示覆盖表的实现程度 | ✅ 定义的命令与覆盖表合并（未实现的命令只来自覆盖表），按首个命令节点分组；程度筛选与全文搜索；34465A 233 条（实现 56 · 接受 10 · 未实现 167） |
| 内部结构：只读组件图，与 #20 相同 | ✅ 复用 `InternalsView`，数据来自同一个临时会话 |
| 试运行：只含这台仪表的临时会话，发命令看回复与错误队列，可重新上电；临时会话与打开的测试台互不影响，不写运行记录 | ✅ 新增读接口 `GET /api/try/{sid}/instrument` 与 `GET /api/try/{sid}/internals`；页面打开时建立临时会话，每 3 s 触碰以免被回收；重新上电即换一个新会话（状态、错误队列与读数归零） |
| 用例：列出工作区中针对它的用例文件，一键运行，逐条显示步骤、期望与实际 | ✅ 接口来自 T2；页面列出文件、运行并展开每步；全部通过时只显示通过数，有失败时显示失败数 |
| 修改记录：版本、来源、时间与用仪表语言写的变更说明；可以对比任意两版；文件变化后自动刷新并提示 | ✅ 每 2 s 轮询定义的 sha256（`GET /api/definitions/{id}/document`），变化后重取定义与覆盖表、重开临时会话，并提示“已载入新的修改”；新建文件的第一版按 D35 记为“外部修改”（Agent 保存前的内容），变更较长的列表折叠前 8 项 |
| 页面没有任何编辑定义的入口 | ✅ 端到端测试断言没有“编辑定义/保存定义” |
| 端到端测试 | ✅ `web/e2e/instrument.spec.ts` 3 项：概览/命令/试运行/用例（通过 2、失败 1 并显示期望与实际）、Agent 保存后自动刷新与变更说明、工作台标签页入口（“验收”与“内部结构”并列） |
| 视觉基线 | 不新增 Storybook 故事，现有截图不变（页面由端到端测试覆盖） |
| `scripts/check.sh` | ✅ 见提交前运行结果 |

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

## 命令头写常量：CONFigure 的功能选择（Issue 117）

| 完成条件 | 自检 |
| --- | --- |
| 声明式写法 | ✅ 多状态命令新增 `writes: {<设置>: <常量>}`：参数全部校验通过后先写常量、再写参数，任一不合法整条命令不生效；查询只回答参数。`docs/instrument-authoring.md` 2.8 |
| 34465A 的 CONFigure | ✅ `:CONFigure[:VOLTage]:DC` 写 `sense.function: VOLT`，`:AC` 写 `VOLT:AC`；手册示例 `CONF:VOLT:AC 100` + `READ?` 不再需要额外的 `FUNC` 命令。手册用例改为验证功能切换与量程/分辨率，覆盖表说明收窄 |
| 静态校验 | ✅ `E-DEF-WRITE`：常量必须是枚举选项、布尔量或范围内的数；引用的设置不存在报 `E-DEF-STATE-UNKNOWN`；`writes` 只能与写设置的参数同用（模型校验）。`tests/test_declarative.py` 4 项 |
| 现有定义不受影响 | ✅ 仅 34465A 使用新字段；`scripts/check.sh` 全绿 |

## 无参数命令：set 调操作、query 读状态；无参数置态（Issue 119）

| 完成条件 | 自检 |
| --- | --- |
| 同一命令头：set 调操作、query 回读状态 | ✅ 命令新增 `read: <设置>`（只与 `op` 同用）：query 不调用操作，按 `reply` 模板回读该设置，`{value}` 走定义/命令的格式（Fluke 的 numeric 布尔回 `1`/`0`）。Fluke 5522A 的 `OPER` 加 `read: out.operate`，`OPER?` 回 0/1（`tests/test_fluke_5522a.py`、手册用例） |
| 无参数、只置态的命令 | ✅ 状态命令新增 `set_value`：没有参数时写入该常量，`query: false` 时没有查询；带参数的 set 仍按参数解析。Fluke 的 `LOCAL` / `REMOTE` / `LOCKOUT` 写入 `interface` 状态（`tests/test_fluke_5522a.py` 检查状态；测试校准仪的同类命令在 `tests/test_calibrator.py`） |
| 静态校验与文档 | ✅ `read` 引用的设置不存在报 `E-DEF-STATE-UNKNOWN`；`read` 只与 `op`、`set_value` 只与无参数状态命令同用（模型校验）；`docs/instrument-authoring.md` 2.7 节写明写法 |
| 现有定义不受影响 | ✅ 只有 Fluke 与测试校准仪使用新字段；其余定义与测试不改通过，`scripts/check.sh` 全绿 |

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
| Keysight 34465A（只写 YAML） | ✅ issue 7：定义 `src/simubench/library/instruments/keysight-34465a.yaml`（reference = Truevolt 系列 Operating and Service Guide 34460-90901, Edition 13, 2025-09；specs 记数据表 5991-1983EN 的直流/交流精度；deviations 按 1 年、10 PLC 的直流精度抽增益与零点），覆盖表 233 条（实现 56 · 接受 10 · 未实现 167），手册用例 `tests/models/keysight-34465a/`（26 条）与输入阻抗对照用例 `tests/models/keysight-34465a-impedance/`（2 条，跑在示例测试台 `examples/benches/keysight-34465a.bench.yaml` 上：SDG2042X 经 900 kΩ/100 kΩ 分压，10 MΩ 与 >10 GΩ 读数不同且与手工计算一致，含求解器每节点 1e-12 S 的 GMIN 与 SDG 的 50 Ω 内阻）；PyVISA 脚本 `examples/scripts/keysight_34465a_measure.py`（CONFigure、BUS 触发、多次 `*TRG`、`R?` 取回）按真机写法、不改一行在测试中运行（`tests/test_keysight_34465a.py`，7 项：脚本、阻抗与 ACV 手工计算、校准 as-found/as-left < 1 ppm、按量程调整、NPLC 计入虚拟时间、偏差抽取）；校准用 `calibration_store`，键 =（功能， 实际量程），出厂安全码 AT3446XA；`src/simubench/library/components/` 无改动（#93 检查通过）。CONFigure 的命令头选择功能（`writes`，issue 117）。与手册的已知偏差逐条写进覆盖表说明（CONFigure 手册的“恢复触发默认值”未实现、读数时间单一映射、AC 沿用 DC 表，TRIG:DEL 在每触发前而非每测量间，校准错误码 701/702 对应手册 702/703/708 等）。`scripts/check.sh` 全绿 |
| Fluke 5522A（只写 YAML） | ✅ issue 8：定义 `src/simubench/library/instruments/fluke-5522a.yaml`（reference = Fluke 5522A Multi-Product Calibrator Operators Manual，PN 3795084, January 2011，从 fluke.com 下载的 PDF 核对；`*IDN?` 格式 `FLUKE,5522A,<序列号>,<主 CPU+前面板 CPU+inguard PGA 固件>`；builtins 限定为手册的公共命令——:SYSTem:* 一类 SCPI 头不是 5522A 的命令，错误队列用 `ERR?`/`FAULT?`（`system.next_error`，回复 `{code},"{text}"`，空队列 `0,"No Error"`），日期时间用 CLOCK）；输出经 `calibrator_output`：`OUT 10 V` 直流、`OUT 1 V, 1 kHz` 交流、`OUT 60 HZ` 只更新频率（quantities：units V/HZ，combine "V"→DCV、"V,HZ"→if_zero、"HZ"→空更新；缺单位报手册的 519、未知单位 1305、同单位两次（双输出未建模）506）；量程 ±1020 V / 500 kHz（超限报 -222，SCPI 码——手册的 1300 系列错误码无法由状态范围检查给出）；待机上电与 *RST、待机输出端断开进电路求解、z_out 5 mΩ、i_max 5 mA（过载跳回待机报手册的 1503 "Output current lim exceeded"）、settle 5 s（手册 <5 s，`*OPC?`/`*WAI` 等待并计入虚拟时间）、`standby_above` 33 V（≥33 V 改设定回待机需再 OPER，手册第 3/4 章）；隐藏 gain/offset 作真值，deviations 取 1 年指标最紧档（增益 20 ppm、零点 1 µV，标量——calibrator_output 无按量程表，DC/AC 两套限值表达不了）；1 年精度规格表记 specs。覆盖表 100 条（实现 16 · 接受 5 · 未实现 79），手册用例 `tests/models/fluke-5522a/cases.yaml` 27 条（跑在带 sb-dmm-1 负载的 bench 上，一致性检查通过）；示例台 `examples/benches/fluke-5522a.bench.yaml`（5522A → SDS2104X Plus：直流 10 V 与 1 kHz 交流 1 V 在示波器可见，含 8-bit ADC 量化），精度台 `validation/benches/fluke-5522a-dmm.bench.yaml`（deviation: from_specs：OPER 后读设定值、误差在 ±20 ppm + 1 µV 内且随种子可复现，STBY 后 0 V）；`tests/test_fluke_5522a.py` 13 项。`OPER?` 回读工作状态、`LOCAL`/`REMOTE`/`LOCKOUT` 保存接口状态（#119）。与手册的已知偏差逐条写进覆盖表说明：`OUT` 裸数值形式（沿用当前单位）不建模、`ERR?` 队列溢出按 SCPI-99 报 -350（手册为仪器码 1）、正错误码在 *ESR? 记为 DDE 位（手册的 CME/EXE 类）、NORMAL LO 是机内地不单独成端口、EARTH/EXTGUARD 只接受不影响仿真。`src/simubench/library/components/` 无改动（#93 检查通过）；`scripts/check.sh` 全绿 |
| Rigol DL3021 电子负载（只写 YAML） | ✅ issue 194：定义 `src/simubench/library/instruments/rigol-dl3021.yaml`（reference = *DL3000 Series Programmable DC Electronic Load Programming Guide* PGJ01106-1110, Oct. 2025，软件版本 00.01.06；`*IDN?` = `RIGOL TECHNOLOGIES,DL3021,DL3000A000001,00.01.00.04.05`，四字段格式按手册示例；DL3021 的量程与额定值按 DL3000 数据表：CC 4 A/40 A、CV 15 V/150 V、CR 0.08 Ω–15 kΩ、200 W、最小工作电压 1 V @ 40 A → 拐点电阻 0.025 Ω）。五个 C1 组件按 component-map 的块：`eload_power_stage`（r_min/i_max；p_max 交给 200 W 的 OPP 保护）、`eload_mode_loop`（CC/CV/CR/CP、斜率、Von）、`eload_input`、`eload_meter`、`eload_protection`。覆盖表 127 条（实现 41 · 接受 12 · 未实现 74）：实现 CC/CV/CR/CP、四种设定值与量程、斜率、Von、输入开关、V/I/P 回读（MEASure/FETCh，含 200 ms 积分时间与 FETCh:TIME?）、V_LIMIT/ILIM（作为 OVP/OCP 阈值）与 200 W 的 OPP；接受显示/系统/接口类（*PSC、触发源、状态使能与预置、SENSE、LXI）。手册用例 `tests/models/rigol-dl3021/cases.yaml` 18 条（跑在 10 V、0.1 Ω 直流源的 `bench.yaml` 上）全部通过；示例台 `examples/benches/rigol_dl3021.bench.yaml`（SDG2042X DC 5 V → DL3021：CC 1 mA 读 4.95 V、4.95 mW，CV 2 V 读 60 mA/0.12 W）；真机写法的 PyVISA 脚本 `examples/scripts/rigol_dl3021_measure.py` 在测试中运行（`tests/test_rigol_dl3021.py` 5 项：脚本、CC 斜率按虚拟时间、OVP/OCP 跳闸、OPP 在 210 W 跳闸）；`tests/test_model_components.py`、`test_block_coverage.py` 通过，`src/simubench/library/components/` 无改动；`simubench validate` 无诊断，`scripts/check.sh` 全绿。与手册的已知偏差逐条写进覆盖表：`:SOURce:FUNCtion?` 回短格式 CURR/RES/VOLT/POW（真机回 CC/CV/CR/CP；平台的状态枚举只有一种写法，设置词与回读词不能分开）；四种模式共用一个 V_LIMIT/ILIM 设定（真机按模式独立记忆）；OVP/OCP 阈值借用 V_LIMIT/ILIM、OPP 取额定 200 W（手册没有单独的保护阈值命令）；保护跳闸后没有远程清除命令（真机用前面板 OK）；状态寄存器的条件/事件位与 LIST/电池/OCP/OPP 测试/动态（C2）未建模；读数由组件写成 7 位尾数、测量各计 200 ms；`*TST?`/`*OPT?` 返回固定内容 |
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
| G3 计量：档位表的增益与偏置误差、时钟 ppm、通道时延、个体偏差与校准常数（issue 135） | ✅ `scope_vertical` 的 `gain_error_ppm` / `offset_error` 按输入端 V/div 给（数或 `deviations` 的表），`dac_error_ppm` 折算到显示偏置；`scope_probe` 的 `skew` / `skew_error` 是 ADC 后的数字延迟，触发不受影响；`scope_timebase` 的 `clock_ppm` / `rosc` / `ref`（REF_IN 走 `input_port` + 内部连线，与频谱仪参考输入一致；正 ppm 为时钟快、时间读数偏低）；`calibration_store` 新增 `calibrate`/`state_key`，自校准一次写入所有档位，`scope_vertical.self_calibrate` 按当前探头比换成显示刻度；通用 op `scope_acquisition.self_cal` 由型号的 `reply` 模板给命令文字。`tests/test_scope_acquisition.py` 4 条（REF_IN 与回退、每档增益/偏置/DAC 误差与探头比、SKEW 与时钟 ppm 的个体偏差随种子抽取）；`tests/test_siglent_sds.py` 7 条（SDS 的 14 档增益在 ±3 % 内且随种子复现、每档增益进入记录、`*CAL?` 写 14 档并使读数回到一个 ADC 码内、SKEW 移相 36°、时钟 20000 ppm 在频率测量中可见、5522A 的“核查 → 调整 → 复核”契约用例、无偏差时行为不变）；契约用例在 `tests/models/siglent-sds2104xplus/calibration-cases.yaml`，用 `simubench cases` 的同一运行器执行。SDS 的 `*CAL?` 回复 `*CAL 0` 取自同系列编程手册（EN11F 目录只有 `:SYSTem:SELFCal`），覆盖表注明未核实；手册没有偏置误差与 SKEW 精度，不编造偏差限值。Python 842 项通过（另 3 项跳过），`scripts/check.sh` 全绿 |
| G7 低频校准参考示波器改用独立模块（issue 139） | ✅ `lfcal-reference-scope.yaml` 显式写出 `scope_timebase`（记录恰为 MSIZ 点）、`scope_trigger`（每通道的电平与触发沿）、`scope_adc`（12 位、±5 格、20 GSa/s）、每通道 `scope_vertical` 与 `scope_probe`（探头比走 `$ch.probe` 的默认绑定），测量在 `meas`；改写前后定义指纹相同，仓库里不再有经宏展开的定义。以它为宏形式样本的两个测试（宏展开的诊断指针、测量搬移到 `meas`）改为从显式形式构造旧写法，其余不改：`tests/test_lowfreq_cal.py`、`acceptance/test_lowfreq_cal_scenario.py`、`examples/lowfreq-cal/cases.yaml` 与 `tests/test_lfcal_mcp_rebuild.py` 通过，`library/components/` 无改动。Python 917 项通过，`scripts/check.sh` 全绿 |
| G6 测量与传输模块；SDS2104X Plus 只用通用模块（issue 138） | ✅ 新模块 `scope_measurements`（测量名表、顶底求法、计周期电平与迟滞、最少周期、削波、门控、统计）；`waveform_transfer` 增加声明的前导（二进制按偏移、文本按顺序）、float32/64 块、点数模式、每次读取上限的三种行为；采集的状态文字、带次数的采集方式与只读数值，触发电平与延迟的范围，探头命令都改为参数。`siglent_sds_acquisition`、`siglent_sds_channel_limits` 删除，#93 例外清单去掉两项；SDS2104X Plus 对 105 条命令的回复与错误码改写前后逐字节相同，手册用例、`tests/test_siglent_sds.py` 与示波器验收不改通过。测量写在采集上的旧写法经宏移到 `meas`，低频校准示例不改通过。类 R&S 的契约定义 `tests/models/contract-float-scope/` 只用通用模块，10 条用例通过；`tests/test_scope_g6.py`。内部结构图加入新模块名称，端到端 42 项通过，内部结构图的暗色基线更新 |
| G5 触发路径：独立检测、亚采样插值、NORMal 等待推进虚拟时间（issue 137） | ✅ `scope_trigger` 在模拟信号上检测，带耦合滤波、噪声、迟滞与噪声抑制、释抑、电源与外触发；触发时刻在采样之间插值，小数与抖动进入记录的 xorigin。`tests/test_scope_trigger.py`（6 项）：无抖动时边沿相对触发的偏差小于百分之一采样（改动前散布一个采样），20 ns 抖动的统计 σ 为 20 ns（±20 %）；1 Hz 正弦的单次采集在约 1 s 后的下一个上升过零触发（误差 1 µs 内），记录填满后完成，状态为 Stop；噪声抑制关闭时带噪声的慢边沿在同一边沿上再次触发，开启后下一次触发在一个周期后；AC / 高频抑制耦合、电源触发、释抑各一项。依赖旧行为的验收与低频校准用例按真机行为改正。SDS2104X Plus 的触发耦合等命令未接上（取值需按手册核实）|
| G4 噪声随档位；平均、峰值检测、高分辨率在码值上处理（issue 136） | ✅ `scope_vertical` 增加 `noise_table`（每输入档的 V rms，地板在前；`scope_acquisition.noise_rms` 删除，σ 取自通道的 vertical，随机流仍以采集为键）；处理级 `processing`（normal/average/peak/eres，SDS 由 `:ACQuire:TYPE` 映射）：average 是 N 次各自加噪、量化的采集的均值（量化噪声随平均下降，时间 ×N），peak 每记录间隔按原生速率取至多 `peak_max_oversample`（64）个码、保留偏离间隔两端最远的极值（记录速率跨过的脉冲进得来），eres 是 `4^b` 个码的居中 boxcar（噪声降 2^b）。SDS2104X Plus 每档噪声为估计值 max(35.8 µV, 0.015 × 档位)（specs.noise_per_division、noise_table、覆盖表均标明估计，D46），PEAK/ERES 改为实现。`acceptance/test_noise_scenario.py`：各档 σ 与档位表一致、0.5 V/div 下 5 mV 直流平均 64 次 MEAN 误差 < 0.2 LSB（旧模型测得 0）、ERES 2.0 的 σ 为 1/4、NORMal 漏掉的 500 ns 脉冲 PEAK 全部保留、含平均/PEAK/ERES 的重放逐位一致。`refcalc.noise.averaged_codes_sigma` 给出码值平均的精确 σ。依赖「极值无噪声」的用例按真机行为改正（SDS 手册用例 PKPK、5522A 例程、SDG 负载测试、手册流程的偏差界），逐条见 PR |

## 面板布局的声明与真机式面板；万用表、校准源的类别默认布局（Issue 128，instrument-authoring.md 7.3）

| 验收标准 | 自检 |
| --- | --- |
| 定义的 `display.panel`：分组、控件（按键组、带单位与步进的数值、开关、下拉）、主显示区的读数与单位、软键 | ✅ `models/definition.py` 的 `Panel`；会话接口的 `display.panel` 与 `panel_source`（`library/panels.py` 的 `effective_panel`）；`tests/test_panel_layout.py` |
| 静态校验与诊断码，有反例 | ✅ `E-DEF-PANEL-STATE / CONTROL / VALUE / READING / ACTION`、`W-DEF-PANEL-IGNORED`；同一测试文件 19 个反例（不存在的设置、类型不相容的控件、只读设置、缺少 keys、枚举短形式给出改正建议、超出范围的值、不存在的组件、无人执行的动作、专用屏幕类别） |
| 万用表与校准源的类别默认布局；其他类别用通用布局；专用屏幕不变 | ✅ 34465A：所选功能的读数、DC V / AC V、100 mV–1000 V 与 AUTO、NPLC、输入阻抗、AC 带宽与触发设置；5522A：输出值、ACV 时的频率、OPER / STBY；`sb-dmm-1` 只有读数；示波器、函数发生器等无面板 |
| 端到端：34465A 切换功能与量程，读数随之变化；5522A 设置输出、工作与待机；带声明的测试定义按声明出现分组与控件；搭建模式下改的是初始设置 | ✅ `web/e2e/panel.spec.ts`（4 项）：1000 V 量程下同一输入读数由 99.103 mV 变为 99.0000 mV、AC 显示 0；OPER 后经过 5 s 建立时间万用表读到 2.5 V；`sb-panel-1`（e2e 专用定义）的分组、按键、步进、下拉、开关与软键；搭建模式下按 10 V 量程写入测试台文件（`sense.range: 10`、`sense.range_auto: false`），运行中的仪表不变。远程状态沿用设置接口（#121） |
| 视觉基线（亮色与暗色）；schemas 与前端类型重新生成；`scripts/check.sh` 全绿 | ✅ 新故事“Instruments/Front panel”（万用表、校准源，各亮暗两张）；原有基线不变；端到端 42 项通过 |


## 示波器的开发验证基准：Keysight DSO-X 2024A（Issue 151，D45）

| 验收标准 | 自检 |
| --- | --- |
| 定义只用通用组件，`reference` 写明编程手册版本；`*RST` 的示例回复逐条一致 | ✅ `keysight-dsox2024a.yaml`（InfiniiVision 2000 X-Series Programmer's Guide 02.66.0000，数据手册 5990-6618EN）；`:CHAN1:RANG +40.0E+00`、`:TIM:…RANG +1.00E-03`、`:TRIG:…HOLD`、`:WAV:UNS 1;BYT MSBF;FORM BYTE;POIN +1000` 等示例回复相同；释抑默认取复位表的 40 ns（示例写 60 ns，覆盖表注明） |
| 覆盖表由命令索引生成，实现的命令都有手册用例 | ✅ 485 条：实现 67、未实现 418；`tests/models/keysight-dsox2024a/cases.yaml` 17 条用例通过 |
| 按手册写的 PyVISA 脚本在 SimuBench 上读回测试台的信号 | ✅ `examples/scripts/keysight_dsox_read_waveform.py`、`examples/benches/keysight-dsox2024a.bench.yaml`；`tests/test_keysight_dsox.py`（10 项）：BYTE / WORD / ASCii 三种格式按前导重建的电压与记录一致、与网络上的信号相差在半个码加噪声内；RC 低通后的幅度；`:DIGitize` 后 `*OPC?` 时采集已完成；xorigin 带亚采样的触发偏移；数据手册的增益、偏置、DAC 与时基个体偏差在限值内且随种子复现；偏置超出范围钳到最近值并随档位收紧；RANGe 是档位乘格数；探头比换算档位与偏置 |
| 为此补的通用能力有单元测试 | ✅ `tests/test_number_formats.py`（14 项）：`significant` 的工程记数、命令 `factor`、保留的空洞码、头字段的 `when` / `else`、参考位置决定触发前的格数；`ascii_block`、16 位字的 y 增量由上面的脚本测试覆盖 |
| D45、real-models.md 记录基准的变更；schemas 与前端类型重新生成；`scripts/check.sh` 全绿 | ✅ Python 892 项通过（另 3 项跳过） |

## 真实型号：Keysight DSO-X 2024A 按手册补齐常用命令（第一批，Issue 160，D42）

| 验收标准 | 自检 |
| --- | --- |
| 定义按手册补命令，只用通用组件（#93 检查） | ✅ `keysight-dsox2024a.yaml` 新增 `*TRG`（手册：与不带参数的 `:DIGitize` 相同）、`:TRIGger:FORCe`（`acq.force_trigger`）、`:SYSTem:PRESet`（`sys.reset`，重置条件与 `*RST` 相同）、`:SERial?`、`:ACQuire?`（手册的 `*RST` 示例回复逐字一致）、`:SYSTem:DIDentifier?`，以及 8 条“接受”（`:TIMebase:VERNier`、`:TIMebase:WINDow:SCALe`/`:RANGe`/`:POSition`、`:SYSTem:LOCK`、`:SYSTem:PROTection:LOCK`、`:SYSTem:MENU`、`:SYSTem:DSP`）；`:ACQuire:TYPE` 的 PEAK 与 HRESolution 改由 `scope_acquisition` 的处理级实现（HRESolution 的 8 码 boxcar 是 SimuBench 值，覆盖表注明）。`src/simubench/library/components/` 未改，`tests/test_model_components.py` 通过 |
| 覆盖表与用例 | ✅ 487 条：实现 74（67 → 74；新增 6 条、补上原先漏掉的 `:RSTate?` 与 `:ACQuire?`，并把 `:SYSTem:ERRor[:NEXT]` 从“未实现”改正）、接受 8、未实现 405；每条“接受”标 `affects_data: false` 并说明。`tests/models/keysight-dsox2024a/cases.yaml` 30 条（17 → 30），每条新命令至少一条，`manual` 引用手册章节；`simubench cases` 30/30 通过 |
| 影响数据的命令有行为测试 | ✅ `tests/test_keysight_dsox.py` 14 项（新增 4）：`*TRG` 在停止时也采一帧并让 `*OPC?` 等到记录填满、`:TRIGger:FORCe` 在触发条件不满足时仍采一帧（记录的 `triggered` 为假）、`:SYSTem:PRESet` 恢复 `*RST` 条件并让示波器重新运行、PEAK 保留记录速率跨过的摆幅（40 MHz 正弦、50 ms/div：NORMal 混叠成单一电平，PEAK 到满摆幅） |
| 表达不了的命令在覆盖表写原因，需要新能力的登记 | ✅ `:AER`/`:TER`/`:RSTate?`/`:OPEE`/`:OPERegister*`（运行与触发状态寄存器）、按参数选通道的 `:BLANk`/`:VIEW`/`:STATus`/`:TRIGger:LEVel <level>,<source>`、分段存储、其它触发类型、`:SYSTem:DATE`/`TIME`、RLOGger、SETup 均写明原因；能力 issue #248（`核心`）记录缺口 |
| 检查 | ✅ `uv run simubench validate` 无诊断；覆盖表一致性检查通过；`scripts/check.sh` 全绿（pytest 1243 项通过、6 项跳过；ruff、生成 schema、Web 类型与测试通过） |

## 仪表模板机制与示波器模板；MCP 模板工具（Issue 125，instrument-authoring.md 7.4，D3）

| 验收标准 | 自检 |
| --- | --- |
| 模板是生成函数加输入的 JSON Schema（中英文标签、单位、说明）；展开为普通定义，记下 `template: {id, version, inputs}`，加载与运行时忽略 | ✅ `library/templates/`、`InstrumentDefinition.template`；`tests/test_templates.py` 检查 schema 中每个字段都有中英文标题与说明，去掉记录后定义相同 |
| 示例输入展开后没有诊断；接入信号源后波形读取、自动测量、触发、单次采集同步正确，个体偏差按种子复现 | ✅ 按前导重建的电压与记录一致、记录在 8 位 ADC 的码格上；VPP 与 1 kHz 频率；`:SINGle` 布防后在虚拟时间中完成、触发在参考位置；增益、偏置、DAC 与时基偏差随种子复现且在档位表的限值内 |
| 用模板写出与 `sb-dso-4` 等价的示波器，`sb-dso-4` 的用例在其上通过 | ✅ 验收场景的命令与更多（三种格式的波形、八种测量、触发电平与沿、单次、耦合、阻抗、带宽限制、超范围、未知命令，共 56 条，34 条查询，32 条有回复）回复与错误队列逐字节相同 |
| 输入错误各有反例，诊断指向输入字段 | ✅ 档位表非递增、偏置范围缺档、偏置范围没覆盖到最大档位、交织映射指向不存在的通道、通道出现在两组交织、上电点数不在列表中、未知测量、超出 schema 范围、缺少字段、未知字段，共 10 个 |
| MCP 协议级测试覆盖三个工具；不新增型号专用组件 | ✅ `tests/test_mcp.py::test_an_agent_builds_a_scope_from_a_template`：列出、取 schema 与示例、错误输入不保存、保存后版本历史来源为 agent、按新输入重新展开与过期哈希的冲突；生成的定义只用 `generic` 组件 |
| schemas 与前端类型重新生成；`scripts/check.sh` 全绿 | ✅ |

## 信号源的组件粒度与信号源模板；幅度单位 dBm（Issue 144，instrument-authoring.md 7.4）

| 验收标准 | 自检 |
| --- | --- |
| 波形合成、幅度与偏置的定义、输出级、参考时钟各自成模块；现有信号源定义改用它们，行为不变 | ✅ `waveform_synth`、`amplitude_level`、`output_stage`、`reference_clock`；`sb-awg-2` 与低频校准仪改写后全部测试不改通过（`sb-awg-2` 的用例、低频校准的契约用例与验收） |
| 示例输入展开后没有诊断；`sb-awg-2` 与低频校准仪可以用模板等价写出，各自的用例通过 | ✅ `tests/test_signal_source.py`：`sb-awg-2` 的回复与 50 Ω 上的输出相同；校准仪替换后 `examples/lowfreq-cal/cases.yaml` 全部通过 |
| dBm 与 VPP、VRMS 的换算（50 Ω 与高阻） | ✅ 0 dBm、10 dBm 在 50 Ω 负载上的功率；VRMS 的正弦与方波；高阻设定下 dBm 按 50 Ω 换算、开路为负载上的两倍 |
| 百分数占空比 0.1 % 在 10 kHz 与 5.5 MHz 的保真度（谐波截断时带说明，D33） | ✅ 10 kHz：谱线上限截断并带说明，高电平约 100 ns；5.5 MHz：截到 1 GHz 带宽，谱线与解析傅里叶系数一致；两者均值精确 |
| 输入错误各有反例，诊断指向 `template.inputs`；不新增型号专用组件 | ✅ 八个反例；生成的定义只用通用组件 |
| `scripts/check.sh` 全绿 | ✅ |

## 由脚本选模板并预填输入（Issue 145，instrument-authoring.md 5.4、7.4）

| 验收标准 | 自检 |
| --- | --- |
| 命令族识别：模板的每个方言变体声明自己的命令特征，扫描脚本时按命中数选出模板与变体，列出依据的行号 | ✅ 模板的 `variants` 展开为变体定义，用 `scriptscan.rank` 计数；示波器 `scpi-scope` / `lecroy-scope`，信号源 `scpi-source` / `channel-base` |
| 预填输入，每项标明来源（脚本第几行）或“默认值，待确认” | ✅ `filled` 的 `source` 为 `script`（带行号与说明）或 `default`；型号串取自 `--scope-model` 等的默认值；占空比按写入的值推断比例或百分数并提示 |
| 测试台草稿：连线按通道对应关系给出建议（标为推断），运行方式给出确定性的默认 | ✅ `inferred_wires`；`run: {seed: 1, clock: command, idle: off}` 与说明 |
| `draft_bench_from_script` 在内置库没有匹配时返回模板建议；`create_definition_from_template` 直接接收草稿的输入；展开后仍不支持的命令按行号列出 | ✅ 报告的 `template`；加入一条 `HISTOGRAM_MODE ON` 时按行号报告 |
| 低频校准示例脚本与类 Keysight 示波器脚本：只用内置库与模板，选对模板与变体，预填输入展开后没有诊断，全部命令被支持 | ✅ `tests/test_template_draft.py`；按草稿建好定义与测试台后脚本跑通，幅值 1 Vpp ±2 % |
| 推断项与默认项可区分；MCP 协议级测试；`scripts/check.sh` 全绿 | ✅ |

## 验收：只给脚本，Agent 经 MCP 搭出低频定标台面并跑通（Issue 146，instrument-authoring.md 7.0、7.6 的 T7）

| 验收标准 | 自检 |
| --- | --- |
| 替身 PyVISA 脚本放进 `acceptance/`，只用公开的命令风格：参考示波器（LeCroy 风格）+ 4 通道校准源（按通道 SCPI 树），两个流程：幅值测量（约 10 kHz–5.5 MHz 的窄脉冲，占空比 0.1 %–5 %，毫伏级）与幅值定标（直流–交流，产出校准表） | ✅ `acceptance/lowfreq_cal_host.py`：脉冲占空比按高电平约 200 ns 随频率取 0.2 %–5 %，幅度 50 mVpp；定标流程为 5 个直流电平的均值与 3 个交流幅度，0.5 Vpp 一点分别以 VPP、VRMS、dBm 写入并测得同一波形 |
| 测试扮演 Agent：空工作区、只有内置库与模板、只用 MCP 工具，从 `draft_bench_from_script` 到 `start_session`，不复制定义文件；脚本对拿到的地址运行到底 | ✅ `acceptance/test_lfcal_agent_scenario.py`：工作区里只有 `host.py`；选出 `lecroy-scope` 与 `channel-base`，两份定义经 `create_definition_from_template` 创建，测试台经 `validate_bench`，脚本经 `@py` 连 `start_session` 返回的 SOCKET 地址跑完两个流程 |
| 两台 `*IDN?` 可区分（按测试台 `idn`） | ✅ Agent 以 `edit_bench` 的 `set_identity` 写入两台不同的 `idn`，脚本按型号段校验 |
| 分块回读 12 345、30 000、100 000 点拼接即完整记录 | ✅ 三种点数按 FP 分块拼接，长度恰为点数；整周期方波的逐点对照（含分块边界）证明各块是同一条连续记录 |
| 查询都有应答、设置都无应答 | ✅ 通信日志按仪器配对：每个带 `?` 的写入都有一条读取应答，没有无查询的应答 |
| 同一种子、同一命令序列结果逐位一致 | ✅ 同一测试台部署两次（seed 1），响应与校准表逐位相等 |
| 未建模命令按定义的策略：严格时报错并记录，宽松时忽略并记录 | ✅ 示波器定义取严格：脚本外的一条未知命令排入 -113 并在事件历史中记录；校准源取宽松：脚本的蜂鸣器探询被忽略，日志记 `ignored`（D16） |
| 故意加一条模板不支持的命令：报告给出行号，Agent 用 `draft_definition_from_script(base_definition=...)` 补上后通过 | ✅ 脚本的 `PERSist 5` 按行号报告；Agent 以模板展开的定义为底起草出 `:PERSIST` 状态与命令，经 `edit_definition` 补上后重扫不再报告，脚本收尾的错误队列为空 |
| 物理：负载 50 Ω / 高阻改变到达示波器的幅度；频率–幅值响应的量级与单调性合理；dBm、Vrms 与 Vpp 一致 | ✅ 负载设定高阻时进入示波器 50 Ω 输入的幅度减半、进入 1 MΩ 输入翻倍；响应在设值的 ±2 % 内随频率单调不增（1.002 → 0.984）；同一 0.5 Vpp 方波以三种单位写入测得同一幅度 |
| `scripts/check.sh` 全绿 | ✅ Python 979 项（其中新增验收 5 项）、schema 与 Web 检查通过 |

## 幅度的多单位：范围按 Vpp 检查，切单位换算数值（Issue 168，D48）

| 验收标准 | 自检 |
| --- | --- |
| 负 dBm 可以写入，输出与 Vrms = √(10^(dBm/10)·1 mW·R) 一致（有限负载用负载设定，高阻按 `dbm_hiz_ohms`） | ✅ `tests/test_signal_source.py`：-10 dBm 入 50 Ω 实测功率 -10 dBm ±0.02，高阻时开路电压按 50 Ω 参考折算 |
| 任一单位写入越界报 -222，界限按当前单位给出；覆盖 VPP、VRMS、DBM | ✅ 11 VPP、6 VRMS（方波 12 Vpp）、-60 dBm 各报 -222，消息为 `amplitude … out of range [下限, 上限] 单位`，状态回滚到写入前 |
| 波形或负载设定导致越界报 -221 并拒绝 | ✅ VRMS 下正弦→方波跌破 `vpp_min`、方波→正弦超出 `vpp_max`、LOAD 50→1000 使 dBm 波形超界，各报 -221 且波形/负载不变 |
| VPP→VRMS→DBM→VPP 切换：输出不变，查询返回换算值，回到原值（1e-9） | ✅ 状态逐次为 1/(2√2)、+3.98 dBm、1.0（rel 1e-9），示波器测得的 Vpp 每步 1.0 ±2e-3；应答按格式精度（1e-6）核对 |
| 回滚与零幅度规则各有用例 | ✅ 单位监听内部写回的幅度在同一次 `set_state` 被后续监听拒绝时一并恢复（`set_state` 快照回滚，引擎层）；模板含 DBM 时 `min ≤ 0` 报 E-TPL-RANGE，组件在 Vpp ≤ 0 切 DBM 时报 -221、单位不变 |
| #146 的定标流程加负 dBm 点并断言 | ✅ `acceptance/lowfreq_cal_host.py` 的定标表加 -19.03 dBm（50 mVpp 方波）一点，`test_lfcal_agent_scenario.py` 断言测得 0.05 Vpp |
| D48 入 `docs/decisions.md`；模板口径文档更新 | ✅ D48 原文照抄（编号未被占用）；`instrument-authoring.md` 7.4 的幅度口径与多单位说明已更新（`real-models.md` 无幅度范围的记载，无需改） |
| `sb-awg-2` 与低频校准契约用例不改通过 | ✅ 全量 990 项通过，无既有用例改动（`LFCAL_INPUTS` 的 min 按新模板规则 0.0 → 0.001，属模板输入数据） |
| 仓库里由模板展开的定义重新展开 | ⬜ 仓库中没有带 `template:` 记录的定义文件（此前的匹配是 `display.template` 面板键），无需重展 |
| `scripts/check.sh` 全绿 | ✅ Python 990 项（新增 11 项）、schema 与 Web 检查通过；Web 面板无需改动（数值控件本就按无范围处理，`PanelScreen` 的钳制以 ±Infinity 兜底） |

## 错误事件给出命令未被执行的原因分类（Issue 174，tui-design.md 1.1、D18）

| 验收标准 | 自检 |
| --- | --- |
| 错误 / 忽略事件带六种分类：`not_modeled`（覆盖表中标为未实现）、`not_in_model`（覆盖表中没有）、`not_in_definition`（无覆盖表、定义中没有）、`ignored`、`builtin_excluded`（`builtins` 之外的公共命令）、`bad_parameter`（参数超范围或类型不对，附定义给出的范围）；`unknown_command` 退役 | ✅ `tests/test_error_classification.py` 每种一条；`reason` 字段名不变，取值收敛为六种 |
| 每条带出处（定义文件或覆盖表文件，按装载得到的路径）与命令树中最接近的命令 | ✅ `origin`、`closest` 随事件给出；最接近的命令按作者命令头的长短两种拼写比较（difflib，0.6 阈值），`tests/test_error_classification.py` 断言出处与最接近命令 |
| 带覆盖表的定义在运行时按覆盖表查找 | ✅ 引擎按定义文件旁的 `<id>.coverage.yaml` 在首次分类时装载（结果按 mtime 缓存；不在建台时装载，避免把读取开销计入 idle 墙钟，D47）；表缺失或读不了时回退到原行为（真实型号仍记 `not_modeled`），不崩溃 |
| 事件 schema 与前端类型重新生成；MCP `read_log` 返回同样的分类 | ✅ `schemas/` 与 `web/src/api/generated/` 重新生成；`tests/test_mcp.py` 经 MCP 断言分类、出处与最接近命令；运行记录逐字保存事件，回放不受影响 |
| Web 措辞覆盖六种分类（中英文） | ✅ `web/src/i18n/{zh,en}.ts` 的 `errors.reasons`；事件面板与运行记录页对 error / ignored 事件显示措辞、参数与范围、最接近命令与出处 |
| 回复文字 | ✅ 当时保持不变；#172 之后改为中性的 "not implemented in this definition"（见下文），分类不受影响 |
| 六种情况各有内置真实型号与工作区定义的覆盖 | ✅ not_modeled / not_in_model / builtin_excluded / bad_parameter 用内置真实型号（Siglent SDS2104X Plus、Fluke 5522A），not_in_definition / ignored / bad_parameter 用工作区定义（`sb-smu-1`、`examples/lowfreq-cal/`） |
| `scripts/check.sh` 通过 | ✅ Python 1002 项（新增 8 项）、schema 与 Web 检查通过；`tests/test_siglent_sds.py` 的 `*CLS` 断言改为 `builtin_excluded`（分类按设计变更，应答未变） |

## 依据由作者提供；未实现命令的中性措辞（Issue 172）

| 完成条件 | 自检 |
| --- | --- |
| 声明与文档 | ✅ 编辑 D42（不新增）：`reference` 是作者选定并提供、维护的依据（厂商手册、设计规格或接口契约）；带 `reference` 的定义不论内置或工作区适用同一套规则（未实现报错、不允许 ignore、覆盖表与用例一致）。`docs/real-models.md`、`docs/instrument-authoring.md`、`docs/glossary.md` 与 schema 的 `Reference` 说明同步 |
| 未实现命令的回复 | ✅ `-113,"Undefined header;not implemented in this definition"`：说明这个定义尚未实现，不把责任归给 SimuBench；事件分类仍为 `not_modeled`，出处是定义或覆盖表。全部真实型号用例、错误分类测试与文档随之更新；`NOT_MODELED` 也用于参数级的未建模提示 |
| 工作区自建定义 | ✅ `tests/test_user_models.py` 5 项：`reference` 指向作者内部规格的定义 + 覆盖表 + 用例，`simubench validate`、`simubench coverage`、`simubench cases`（工作区根目录与子目录中的用例）与 MCP `run_cases` 全部通过；用例的 `manual` 写规格条目；未知命令的回复不含 "SimuBench" 字样 |
| 检查 | ✅ `scripts/check.sh` 全绿（见提交前运行） |

## pytest 全量回到 60 s 预算内（Issue 209）

| 验收标准 | 自检 |
| --- | --- |
| 贴出 `--durations=50` 并处理耗时靠前的用例（TUI 两个 10 s 以上先查等待/超时），不放宽断言 | ✅ 根因是真实 bug：`/api/sessions/{sid}/events` 在队列空闲时不感知客户端断开（卡在 `queue.get()`），uvicorn 优雅关停等它到强退；TUI 退出也不停连接循环。修后 `test_disconnect…` 12.19 s→2.6 s、`test_file_changed…` 13.64 s→3.5 s、8 处 10 s 的 fixture/finally 关停全部落到 1 s 内；断言一字未动 |
| 必要时引入 `pytest-xdist`（`-n auto`），`scripts/check.sh` 与 CI 一致，确认用例间无共享可写状态 | ✅ 两处均为 `pytest -n 4`（等于 4 核开发容器上的 `-n auto`）；全部端口 0 号动态分配、工作区在 `tmp_path`、用例不写仓库文件（跑后 `git status` 干净） |
| 开发容器上总耗时 < 60 s，不删除、不跳过、不隔离任何测试，用例数不减少 | ✅ 用例数 1057 不变；串行 243 s→172 s（修 TUI 挂起，开发笔记本实测；CI 的 4 vCPU 即验收环境的四路并行），`solve_seconds` 改按调用线程 CPU 计（被调度走不算变慢），阈值不变 |
| Windows 任务（main 上）仍然通过 | ✅ CI 矩阵含 windows-latest（合并后 main 上运行）；xdist 与非阻塞关停均为纯 socket/线程，无信号依赖。合并后 Windows 首跑暴露 `test_amplitude_of_a_sine_reads_the_peaks` 容差未按噪声物理给足（既有边界抖动，非本次引入；AMPL 与 PKPK 的天然差为几 σ + 一个码，原 2% 容差不够），已按同文件惯例 `8σ + 0.5/30` 修正 |
| 新增用例不再把全量拉回 60 s | ✅ AGENTS.md 第 4 步新增规则：全量超过 60 s 必须在当前任务内立即处理，不许留给后续 issue |

## 终端控制台 T4：可复现截图、演示录制、运行记录与文档（Issue 178，tui-design.md 6，D18、D47）

| 验收标准 | 自检 |
| --- | --- |
| `--script` 的步骤：`key`、`scpi`（外部客户端）、`advance`、`wait`、`caption`、`highlight`、`screenshot`；一个步骤里按键与字幕/高亮/停留可组合 | ✅ `tests/test_tui_t4_units.py`（解析与校验：未知字段、空步骤、`1.5s` / `200ms` 时长）、`tests/test_tui_t4.py` 与 `docs/tui/find-the-trip.steps.yaml`；`caption` 与 `highlight` 是修饰字段，可与 `key` 同步（tui-design.md 6 的例子现在合法） |
| 输出 SVG；`--record` 输出逐帧 SVG 与 asciinema `.cast` | ✅ `test_record_writes_frames_and_a_playable_cast`：每个停留按 1/8 s 一帧、SVG 与同名截图相同、每帧一个终端文本事件且时间轴连续；CLI 端到端 `tests/test_tui_t4_e2e.py` 走 `simubench tui --script --record` |
| `idle: off` + 固定种子 → 同一测试台 + 同一步骤逐字节相同的 SVG | ✅ `test_scripted_screenshots_are_byte_identical`（两次运行逐字节比较）；演示另固定 SCPI/HiSLIP 端口与外部客户端的源端口（`scpi.source_port`），日志里的 `脚本 :5401` 因此可复现 |
| 运行记录屏幕（`R`）：列表、摘要、回放并报告第一处不同 | ✅ `screens/runs.py`：定宽列（开始/测试台/时钟/种子/事件/虚拟时长/大小）、选中行与“运行中（本会话）”、回放/摘要框；`test_runs_screen_lists_and_replays` 与 4 张 runs 快照（zh/en × 100×32、80×24）；修掉 `_render` 覆写（Textual 内部方法）与 `float` CSS 两处启动错误 |
| README 与用户文档的 TUI 章节；“找到跳闸”演示 | ✅ README「终端控制台（TUI）」（两种启动方式、按键表入口、截图与 `--script` 命令）；`docs/tui/find-the-trip.steps.yaml` + `validation/benches/find-the-trip.bench.yaml`（预设跳闸）；`docs/tui/find-the-trip{,-error}.svg` 由该步骤生成 |
| CI 重新生成文档截图逐字节一致 | ✅ `test_the_demo_regenerates_the_docs_screenshots`：在固定端口上重跑演示步骤，两张 SVG 与已提交文件逐字节相同 |
| 演示脚本同时产生运行记录，截图中的状态可在记录中复核 | ✅ 同一测试断言 `--record-dir` 里恰有一条记录，含外部客户端 `socket:127.0.0.1:5401` 的 `:SYSTem:ERRor?`、预设跳闸的 `-300` 与 `fault` 事件（来源 `bench`）；CLI 端到端测试也确认只开一个会话、只留一条记录 |
| `scripts/check.sh` 全绿 | ✅ 快速层 1088 项约 45 s；`-m "tui_boot"` 87 项约 44 s；ruff、schema、Web 的 check:api/typecheck/lint/test 全绿 |
| 顺带修掉的问题 | ✅ 跳闸只关 `output`、漏掉 `output.enabled`（`sb-smu-1` 的输出没关，`tests/test_faults.py`）；字幕与底栏两个 bottom dock 落同一行（改为同一个 footer）；`NO_COLOR` 主题用空串当颜色让样式表解析失败、整个 TUI 起不来（改 `ansi_default`，快照测试固定颜色主题）；CLI 无头路径的相对导入、`describe()` 调用、`asyncio.run` 参数位置与重复开会话四处错误（`tests/test_tui_t4_e2e.py`） |

## 试运行一台仪表：MCP try_definition 与 HTTP 的临时会话、用例运行（Issue 126，instrument-authoring.md 7.2、7.4）

| 验收标准 | 自检 |
| --- | --- |
| `try_definition` 对 `sb-dmm-1` 与 `keysight-34465a` 返回的回复，与在测试台上发送相同命令一致 | ✅ `tests/test_mcp.py::test_try_definition_agrees_with_a_bench_session`：两个型号各 4 条命令（`*IDN?`、配置、测量、读），部署的测试台上 `send_scpi` 与 `try_definition` 的回复逐条相同；危险命令在临时会话中不需要 confirm，效果可见（`:SOUR1:FREQ?`） |
| 临时会话存在时打开的测试台不受影响（端口、记录、状态）；空闲超时后被回收 | ✅ `tests/test_server.py::test_try_session_runs_alone_and_leaves_the_bench_alone`（`/api/sessions` 只列测试台、回复一致、收发/面板/设置可用、关闭后 404）；`test_try_session_binds_no_ports_and_writes_no_record`（`scpi_base_port=0` 下不发端口、`record_dir` 为空）；`test_try_session_is_reaped_after_the_idle_timeout`（超时后下一次访问回收） |
| 用例运行结果与 `simubench cases` 一致（含一条失败用例的步骤、预期与实际） | ✅ 运行器加 `collect`（`tests/test_cases.py::test_steps_are_collected_only_when_asked`，默认报告不变）；HTTP `POST /api/definitions/{id}/cases/run` 逐条返回步骤（`test_definition_cases_are_listed_and_run_with_steps`，失败用例给出步号、命令、预期与实际）；`GET /api/definitions/{id}/cases` 按模型列出工作区用例文件 |
| HTTP 与 MCP 各有测试；`scripts/check.sh` 全绿 | ✅ HTTP 四个新测试 + MCP 三个；974 项 pytest 通过；schemas 与 `web/src/api/generated/` 重新生成 |

## 定义的变更说明：比较两版，生成仪表语言的说明；HTTP 的修改记录接口（Issue 127，instrument-authoring.md 7.2）

| 验收标准 | 自检 |
| --- | --- |
| 每类变化有一个用例：改命令的回复、增删设置、改量程、改组件参数、改偏差限值、只改说明文字 | ✅ `tests/test_definition_changes.py`：回复（`command` / `query.reply`，含旧新值）、增删设置与范围（`state` / `min`）、量程（`meter.ranges` / `DC` 以多重集比较：新增 1000 V、重排不算变化）、组件参数（`input.z_in` 10 MΩ → 1 GΩ）、偏差限值（`meter.gain` / `limit`）、只改说明文字（`description` 一条）；身份、specs、模板输入与 version、端口走同一比较器 |
| 对 `sb-dmm-1` 与 34465A 的真实修改各给出一份说明快照 | ✅ `tests/test_definition_history.py`：sb-dmm-1 改 `z_in` → `[(component, changed, input.z_in, None, 10.0e6, 1.0e9)]`；34465A 的直流档位表增加 0.01 → `[(component, added, meter.ranges, DC, None, 0.01)]` |
| 只改注释或键的顺序时没有说明 | ✅ 重排键后保存：新版本、`changes == []`；外部只加注释的修改以 external 版本进入历史，其说明为空 |
| `scripts/check.sh` 全绿 | ✅ MCP `create_definition` / `edit_definition` 返回 `changes`（`tests/test_mcp.py::test_edit_definition_reports_what_changed`，read 不返回）；HTTP `GET /api/definitions/{id}/history`（每版带相对上一版的说明）与 `GET /api/definitions/{id}/history/changes?base=&target=`（任意两版，base 0 = 创建）；schemas 与前端类型重新生成 |

## SCPI 通道列表参数（Issue 183，#181 的 A）

| 验收标准 | 自检 |
| --- | --- |
| 解析 `(@…)`：单通道、逗号列表、`:` 范围、数字间空格；空列表、重复通道与格式错各有错误 | ✅ `tests/test_channel_list.py` 的五组解析参数、`test_a_repeated_channel_is_an_error`、`test_a_descending_range_is_an_error`、`test_a_malformed_list_is_an_error` |
| 展开语义：写入对列表中的每个通道各执行一次，每通道自己的量程生效；查询按列表顺序逗号拼接 | ✅ `test_a_write_expands_over_the_channels_with_each_channels_own_range`、`test_a_query_answers_the_channels_in_list_order`、`test_an_operation_with_a_list_runs_once_per_channel_and_joins` |
| 作为值保存：`int_list` 状态接收 `(@…)`，查询把连续通道折叠成 `a:b` 回读 | ✅ `test_a_stored_list_saves_and_reads_back_as_a_list`、`test_a_stored_list_reads_back_collapsed` |
| 与 `<n>` 数字后缀、`INST:SEL`/`INST:NSEL` 并存；每条命令至多一个通道列表参数 | ✅ 展开路径按参数声明执行；`docs/instrument-authoring.md` 2.6 写明声明方法 |
| 非法通道报 -114；空列表与重复通道报 -108；倒序范围报 -222 | ✅ `test_a_channel_outside_the_instrument_is_an_error` 与上面两条 |
| schemas 重新生成；`instrument-authoring.md` 写明声明方法；`scripts/check.sh` 全绿 | ✅ 提交前全绿 |
| **已知缺口**：三位「槽位+通道」号域（34970A 的 101–320）在展开路径上仍按 `1..channel_count` 校验，跨槽位范围没有用例 | ✅ 已由 **#207** 补上号域、跨槽位范围与用例（见下节），E2（#191）不再被它阻塞 |

## 就地扩展定义与模板重新展开的保护（Issue 169）

| 验收标准 | 自检 |
| --- | --- |
| 就地扩展一次调用完成保存（`base_definition = definition_id`、`save=True`、带 `base_sha256`），来源 `agent`，进版本历史 | ✅ `tests/test_template_draft.py::test_the_draft_extends_a_template_definition_in_place_and_reexpansion_keeps_it`：补入 `:PERSIST`，最新一条历史来源为 `agent` |
| 过期的 `base_sha256` 报 Conflict，文件不变 | ✅ 同一用例 |
| 在模板定义上补命令后，不带 `discard_edits` 重新展开被拒绝，消息列出新增命令的 pointer，文件不变 | ✅ 同一用例：拒绝且列出 `/commands/<n>` |
| 带 `discard_edits=True` 重新展开成功，补上的命令被移除 | ✅ 同一用例 |
| 未经手工修改的模板定义重新展开，行为与现在一致 | ✅ 同一用例末段 |
| #167 的验收流改为一次工具调用，其余断言不变 | ✅ `acceptance/test_lfcal_agent_scenario.py` 的 `_build` 用 `base_definition` + `save` + `base_sha256` 一次保存 |
| #46 的既有起草流程（首建）不改通过 | ✅ `tests/test_mcp.py` 的起草用例 |
| `scripts/check.sh` 全绿 | ✅ 提交前全绿 |

## S1：component-map v1 与首波 8 类的块清单（Issue 201，#181 第 1 步）

| 验收标准 | 自检 |
| --- | --- |
| 机器可读块清单 `docs/blocks/<class>.yaml`（`schema: simubench.blocks/1`），按定义的 `class` 字段对应；一块可接受多个组件类型，尚不存在的组件类型允许入清单 | ✅ 12 份（已有 4 类 + 首波 8 类）；`tests/test_block_coverage.py` 的 `test_block_lists_are_well_formed` 与 `test_a_new_definition_must_cover_every_block` |
| 块覆盖测试与例外清单：内置定义覆盖类别的每一块；`EXEMPT` 只能缩短 | ✅ 两台示波器全覆盖；34465A 的三个缺口（E1 #190）、SDG2042X 的四个缺口（第二波）在例外清单中；`test_builtin_real_models_cover_their_blocks` |
| `docs/component-map.md` 的方法：真实框图、依据、求解层级、债务；首波 8 类逐类一节 | ✅ 已有 4 类 + smu、power_supply、electronic_load、spectrum_analyzer、rf_generator、lockin、daq、frequency_counter；各节写明手册名、文档编号与版本，或注明“无公开框图，按类别架构” |
| 回填型号 issue #193–#200 的常用子集、`*IDN?`、手册来源 | ✅ 各 issue 的「S1 回填（#201）」评论；B2901A 的手册库文件是 User's Guide，命令参考另为 B2910-90030（Edition 2, July 2011） |
| D50（粒度与命名）与 D42（2450 第二波）落地；AGENTS.md 与 real-model/SKILL.md 同步 | ✅ #204 已合并；SKILL.md 的「能力 issue 挂 #181」与块清单步骤已同步 |
| `scripts/check.sh` 全绿 | ✅ |

## 通道列表的号域：槽位+通道的号码（Issue 207，#183 的遗漏）

| 验收标准 | 自检 |
| --- | --- |
| 三位槽位号域的定义：`(@101:103)` 展开写入与查询各有用例；非法槽位/通道报错 | ✅ `tests/test_channel_list.py` 的 DAQ 定义（`channels.numbering` = 101、102、201、202）：`test_a_write_and_a_query_use_the_declared_numbers`、`test_a_default_channel_list_uses_the_declared_numbers`、`test_a_suffix_uses_the_declared_number`、`test_a_number_the_numbering_does_not_declare_is_an_error`（-114）与倒序范围（-222） |
| 跨槽位范围（`(@101:205)`）按 34970A 手册处理：无效通道忽略、首尾必须有效 | ✅ `test_a_range_may_span_slots_and_skips_numbers_the_numbering_does_not_declare`（`(@101:202)` 展开为 4 个已声明通道；首尾未声明 → -114） |
| `instrument-authoring.md` 写明号域的声明方法；号域也作用于 `<n>` 后缀与 `int_list` 状态 | ✅ 2.6 新增「通道号域」；`test_a_stored_list_uses_the_declared_numbers` |
| 号域可校验：号码块覆盖全部通道、号码不重复 | ✅ `test_numbering_must_cover_every_channel`、`test_numbering_must_not_repeat_a_channel_number`；`schemas/instrument.schema.json` 与 `openapi.json` 重新生成 |
| 决定可查 | ✅ `docs/decisions.md` D51 |
| `scripts/check.sh` 全绿 | ✅ 提交前全绿 |

## B1：拆分 smu_core 为真实框图组件（Issue 184，#181 的 B1）

| 验收标准 | 自检 |
| --- | --- |
| 按 component-map 的框图拆：源 DAC → V/I 控制环 → 输出级 → V/I 测量；每块先定求解层级 | ✅ `library/components/smu.py`：`smu_source`（设置/数据：源量程、包络、扫描）、`output_control_loop`（电路决策：D34 的模式与限值，持有 mode）、`smu_output`（电路：HI/LO、sense、浮地、继电器，piecewise 逐点更新模式）、`smu_meter`（电路量程电阻 + 数据：NPLC、A/D、sampler）；`docs/blocks/smu.yaml` 与 component-map 的 smu 节同步 |
| 为 B2 定接口：限值切换控制环可被电源输出级复用 | ✅ `output_control_loop` 不引用 SMU 的任何组件：function/v_level/i_level/i_limit/v_limit/output 全部经参数绑定，输出级把量得的端电压与源电流传给 `update_mode`；`envelope` 是可选引用（SMU 的源量程封顶），电源没有它 |
| D34 限值切换、四线、浮地的行为不变；`sb-smu-1` 与相关场景迁移到新组件 | ✅ `tests/test_smu_migration.py` 的 34 条金标准消息回复与虚拟时间逐条不变；`acceptance/test_smu_scenario.py` 11 项、`test_four_wire.py` 15 项、`test_smu_trigger.py` 7 项通过；`validation/instruments/sb-smu-1.yaml` 与 `tests/definitions/test-smu-trigger.yaml` 已改成四块（测量组件仍名 `core`） |
| 金标准与现有 sb-* 验收场景不改通过；`tests/test_model_components.py` 通过 | ✅ `ALLOWED` 未新增（四个组件都是通用组件）；扫描的点仍按 D34 逐点求解，`run_begin`/`run_done` 恢复被驱动设置 |
| SMU 类别的块覆盖检查（S1 建立）通过 | ✅ `tests/test_block_coverage.py::test_the_validation_smu_is_built_from_its_real_blocks` 对 `sb-smu-1` 逐块检查，六块全覆盖 |
| 界面不坏：Web 专用屏幕与 TUI 仪表屏幕 | ✅ 读数仍在名为 `core` 的 `smu_meter` 上（`core.voltage`/`core.regulation`/`core.buffer_*` 键不变），面板与快照未改；Web 的组件标签补了四个新类型 |
| `scripts/check.sh` 通过 | ✅ 提交前全绿 |


## E1：拆分 dmm_core 为输入端子、信号调理与 A/D（Issue 190，#181 的 E1）

| 验收标准 | 自检 |
| --- | --- |
| 第一步先录金标准（读数与虚拟时间，单独提交），再重构 | ✅ 首个提交 `tests/test_dmm_migration.py`：直流测试台（SMU 作源）34 条消息、交流测试台（信号源）15 条消息，逐条记录回复与虚拟时间；拆分后一条不改仍通过 |
| 只拆新块：输入端子（前/后、耦合）、信号调理（量程、滤波）、A/D（NPLC、分辨率）；`trigger_model`、`reading_buffer`、`calibration_store` 不重做 | ✅ `dmm_input_terminal`（端子、输入阻抗、high_z 按量程）、`dmm_conditioning`（功能、量程、AC 滤波、模拟误差）、`dmm_adc`（NPLC 读数时间、分辨率、校准、读数、sampler）；校准存储接口未改（A/D 转发 function/range/raw/effective_range） |
| 同步改面板取法；34465A 从块覆盖例外清单删去 | ✅ `panels._multimeter` 从调理块取 function/range/ranges、从 A/D 取 digits 与读数、设置控件由三个组件汇总；`test_block_coverage.py` 中 34465A 的三项豁免删除 |
| 金标准测试、sb-* 验收场景与 34465A 手册用例不改通过 | ✅ 1127 项 pytest；`tests/models/keysight-34465a/cases.yaml` 与 `tests/test_keysight_34465a.py` 通过；`sb-dmm-1`、`meter-core-test`、`cal-store-test`、`test-range-meter`、`test-trigger-meter` 与 e2e 的 panel fixture 迁移到三组件 |
| 界面不坏：34465A 的 Web 面板与 TUI 仪表屏幕仍工作；PR 在本地跑端到端与视觉并记录 | ✅ 读数仍在名为 `meter` 的 `dmm_adc` 上（`meter.DC`/`meter.AC` 不变）；`pnpm e2e` 42/42 通过，TUI 启动级 87 项与 24 个快照通过；视觉在本机 macOS 与 Linux 基线有平台差异（未更新基线，main 的 Linux runner 跑） |
| `scripts/check.sh` 通过 | ✅ 提交前全绿 |

## E2：DAQ 背板、多路复用卡与扫描表（Issue 191，#181 的 E2）

| 验收标准 | 自检 |
| --- | --- |
| 首波固定配置（34970A + 槽 1 的 34901A）；插卡表达不引入新格式 | ✅ `sb-daq-1`：`channels.numbering` 101–120（#207），`daq_backplane` 声明三个槽位与槽 1 的 34901A；可配置插卡、数字 I/O 与总加器进缺口清单，不新增文件格式 |
| 多路复用卡：继电器状态、闭合限制、通道到内部 DMM 的切换；**继电器在电路层** | ✅ `mux_card`：20 路 H/L 继电器经触点电阻接模拟总线，总线载内部 DMM 的输入阻抗（取自 `dmm_input_terminal`，高阻量程会改变它）；闭合限制默认 1（扫描时 break-before-make），第二路闭合报 -221；`tests/test_daq.py` 覆盖闭合/查询、限制、加载效应（5 V 经 1 MΩ 分压为 4.545 V）与断开 |
| 扫描表：通道列表、计数与间隔；与内部 DMM、触发、缓冲联动 | ✅ `scan_table` 作为 sampler：列表 × 扫描次数、升序扫描（34970A 规则；写下的顺序保留在 `ROUT:SCAN?`）、每点先断后合、读数带 `CHAN` 字段；`:READ?` 返回通道与读数、`:DATA:POIN?` 计数；扫描间隔是设置（首波计时 = 继电器建立时间 + DMM 读数时间） |
| DAQ 类别默认面板 | ✅ `panels._daq`：刚扫描的通道与读数、扫描表、闭合通道；功能与 NPLC、扫描次数与间隔；Run / Stop 软键（`display.actions`）；`test_panel_layout.py` 的用例 |
| DAQ 块覆盖检查（S1 建立）通过 | ✅ `test_block_coverage.py::test_the_validation_daq_is_built_from_its_real_blocks`：背板、卡、扫描表、内部 DMM、触发、缓冲六块全覆盖 |
| 新组件有单元测试与场景测试 | ✅ `tests/test_daq.py` 11 项：号域（101 与内部序号 1、121 越界）、闭合/限制、加载效应、扫描顺序与次数、空扫描表、间隔、扫描后开路、定义校验；`acceptance/test_daq_scenario.py` 2 项（`acceptance/benches/daq_scan.bench.yaml`：1 V / 2 V / 4 V 经 1 MΩ 的 10/11 分压与升序扫描、闭合限制） |
| 顺带修掉的问题 | ✅ `_parse_params` 的 `channel_list` 参数没有按号域映射（操作命令的逐通道上下文会用外部号码）：改为与 `channel_list` 命令同一套 `channel_index`/`channel_number`，DAQ 的 `ROUT:CLOS (@101)` 因此落到 `ch1.closed`；扫描的每点读数经新增的 `dmm_adc.measure()`，与测量命令一样先做自动量程判定（否则第二路 2 V 在 1 V 档读成超量程） |
| `scripts/check.sh` 通过 | ✅ 提交前全绿 |

## C1：电子负载的输入、功率级、模式环、测量与保护（Issue 186，#181 的 C1）

| 验收标准 | 自检 |
| --- | --- |
| 第一步：重构前录金标准（消息、回复、虚拟时间），单独提交 | ✅ 首个提交 `tests/test_eload_migration.py`：CC/CR 26 条与拐点 5 条逐条记录回复与虚拟时间；拆分后一条不改仍通过 |
| 先移模块，再按框图拆 | ✅ `ElectronicLoad` 移出 `smu.py`，新的 `library/components/eload.py` 拆成 `eload_input`（端子、开关、输入电容，盖章电路）、`eload_power_stage`（r_min 拐点、i_max、p_max 包络）、`eload_mode_loop`（CC/CV/CR/CP、Von、CC 斜率）、`eload_meter`（`load.*` 读数）、`eload_protection`（OVP/OCP/OPP 锁定） |
| CP 语义有决策记录（如适用） | ✅ **D52**：I=P/V 用 D34 的模式迭代（定点）求解；CV 用 `loop_source`；多解以迭代到达者为准，不收敛按引擎上限停止并记警告，不扩展电路求解器 |
| CV/CP、斜率、Von、保护各有用例 | ✅ `tests/test_eload.py` 12 项：CC 与拐点、CR、CV（10 V/10 Ω → 5 V/0.5 A；3 V 源开路）、CP（10 V/1 Ω/20 W → 7.236 V / 2.764 A）、Von、0.5 A/µs 斜率（0/2/4 µs → 0/1/2 A）、OVP/OCP/OPP 触发与清除、面板读数键 |
| 电子负载类别的块覆盖检查通过 | ✅ `test_block_coverage.py::test_the_validation_eload_is_built_from_its_real_blocks`：输入、功率级、模式环、测量、保护五块全覆盖 |
| 界面不坏：LoadScreen 与 TUI | ✅ 测量组件仍名 `load`，`load.voltage/current/power/regulating` 键不变；本地 e2e 42/42、TUI 启动级 87 项与 24 个快照通过 |
| 缺口（记入清单） | ✅ Von 在阈值附近没有滞回、DC 模型可能模式抖动；动态/列表（C2）另排；OTP 与 DL3000 的 OCP/OPP/OVP 命令映射留给 #194（手册的 OCP/OPP 是测试模式命令组） |
| `scripts/check.sh` 通过 | ✅ 1159 项 pytest 全绿 |

## B2：可编程电源的设定、控制环、功率级、输出、回读、保护与组合（Issue 185，#181 的 B2）

| 验收标准 | 自检 |
| --- | --- |
| 输出级组件，每路一块：设定 → CV/CC 行为复用 B1 的限值切换控制环 → 输出继电器/sense → 回读 | ✅ `sb-ps-1` 三路（CH1 +6 V/5 A、CH2 +25 V/1 A、CH3 −25 V/1 A）：`power_supply_setpoint`、`output_control_loop`（复用，新增 `channel_envelope` 接口接通道的功率级）、`power_stage`（v_max/i_max/p_max 包络）、`power_output`（继电器、sense、输出阻抗，盖章的源）、`supply_readback` |
| OVP/OCP 的锁定与恢复 | ✅ `supply_protection`：越限锁定、以 E36300 的 **729** 拒绝开输出、`OUTP:PROT:CLE` 恢复故障前的输出状态；故障后输入停止导通 |
| 组合模式只在输出 2 与 3：跟踪、并联、串联；不做成任意路的通用组合 | ✅ `supply_tracking`：跟踪 `CH3=−|CH2|`、并联/串联 `CH3=−CH2`，两路输出必须关闭才能切换；物理连接由测试台连线表示（并联把负输出交叉、串联共负端），文档写明 |
| 多路输出实例化（D15）；输出时序计入虚拟时间（D12） | ✅ 每路是通道组件实例；继电器动作时间 `relay_s`（初始状态就是输出开也生效），回读含约 0.5 mV 的输出阻抗压降 |
| 电源类别的默认面板 | ✅ `panels._power_supply`：每路的电压/电流回读与保护状态，输出开关与电压/电流设定，输出 2/3 的组合键 |
| 金标准测试与现有 sb-* 验收场景不改通过 | ✅ 类别新建、无金标准；1176 项 pytest 与既有验收场景通过 |
| 新组件有单元测试与场景测试（OVP/OCP 触发、输出 2/3 组合模式） | ✅ `tests/test_power_supply.py` 11 项（CV/继电器、CC、负输出、OVP/OCP 锁定与清除、729、跟踪镜像、切换需关闭、并联 4.12 V/2.06 A、串联 10 V/0.1 A、读数键）；`acceptance/test_ps_scenario.py` 2 项（`ps_combo.bench.yaml`） |
| 电源类别的块覆盖检查通过 | ✅ `test_block_coverage.py::test_the_validation_power_supply_is_built_from_its_real_blocks`：设定、控制环、功率级、输出、回读、保护、组合七块全覆盖 |
| 缺口（记入清单） | ✅ 动态/列表、触发、数据记录与校准不在首波；组合时禁用 CH3 单独编程（手册 720）与设备错误码留给 #198；输出 3 在组合中的端子约定按测试台连线 |
| `scripts/check.sh` 通过 | ✅ 提交前全绿 |

## D1：频谱仪的衰减、混频/参考、中频、检波、视频与迹线拆分（Issue 187，#181 的 D1）

| 验收标准 | 自检 |
| --- | --- |
| 第一步：重构前录金标准（消息、回复、虚拟时间、迹线数据），单独提交 | ✅ 首个提交 `5fbe2cc`：`tests/test_specan_migration.py` + `tests/data/specan_gold.json`（76 步、6 条 1001 点迹线：写/平均/最大保持/冻结/清除）；开头 `:TRAC:CLE` 锚定重启，避免定义装载的墙钟时间影响扫描序号；拆分前在 monolith 工作树复核通过 |
| 按 component-map 框图拆分；每块定求解层级 | ✅ `specan_input`（衰减、过载）、`specan_mixer`（本振/参考误差、频率轴）、`specan_if`（RBW、高斯响应、噪声底）、`specan_detector`（四种检波与对数）、`specan_vbw`、`specan_trace`（扫描时间、RF 历史、迹线模式、标记、显示操作）；50 Ω 输入仍是 `input_port`，参考是 `reference_clock` |
| 求解层级写明：大多数据层，只有输入端口在电路层 | ✅ component-map 频谱仪节写明；组件之间没有内部信号连线（频谱是确定性数据计算） |
| 设置耦合的归属写明 | ✅ 自动衰减 → `specan_input`；自动 RBW → `specan_if`；自动 VBW → `specan_vbw`；自动扫描时间 → `specan_trace`（用 RBW 与 VBW）；参考误差/锁定 → `specan_mixer`/`reference_clock`；过载判断 → `specan_input` |
| `sb-sa-1` 定义与相关验收场景迁移 | ✅ 改为九个组件（rf_in/ref_in/clock/input/mixer/if_filter/detector/vbw/trace）；金标准 2 项、`validation-specan.md` 的验收场景 17 项、`tests/test_model_components.py` 均不改通过 |
| 频谱仪类别的块覆盖检查通过 | ✅ `test_block_coverage.py::test_the_validation_spectrum_analyzer_is_built_from_its_real_blocks`：输入、混频、中频、检波、视频、迹线、参考七块全覆盖 |
| 界面不坏：SpectrumScreen 与 TUI | ✅ 读数与迹线键不变；本地 e2e 42/42、TUI 启动级 87 项与 24 快照通过 |
| 缺口（记入清单） | ✅ N9320B 的型号命令组、触发/门控与列表扫描不在首波（型号 #196）；迹线噪声按基准走，未加参考相位噪声 |
| `scripts/check.sh` 通过 | ✅ 1179 项 pytest 全绿 |

## D2：拆分射频源为真实框图组件（Issue 188，#181 的 D2）

| 验收标准 | 自检 |
| --- | --- |
| 第一步：重构前录金标准（消息、回复、虚拟时间），单独提交；隐藏时基与卡森规则拒绝也录进去 | ✅ 首个提交 `7d240a5`：`tests/test_rfgen_migration.py` + `tests/data/rfgen_gold.json`（132 步）；覆盖载波与电平、AM、FM/PM 到卡森规则、999 条谱线预算的 −222 与状态回滚（含运行中改调制频率触发的那一条）、脉冲调制 −221、超范围 −222、列表与步进扫描（线性/对数）、REF_OUT 开关、EXT 无参考时回退内部、隐藏时基（2 ppm：`:FREQ?` 读设定值而线上每条谱线带误差）、0.5 s 时钟推进；信号记录的是 RF_OUT 与 REF_OUT 两个网上的谱线；所有命令 0 ns。拆分前在 monolith 上通过 |
| 按 component-map 框图拆分；每块定求解层级 | ✅ `rf_reference`（`reference_clock` 加 REF_OUT，时基误差与锁定）、`rf_synthesizer`（载波、列表/步进计划、`:INIT`/`:TRIG`）、`rf_mod_source`（内部低频调制源：三个调制频率随同一时基、FM 的 −π/2 相位）、`rf_modulator`（AM/FM/PM、卡森截断、β 与谱线预算）、`rf_level_control`（dBm→EMF）、`rf_attenuator`（步进衰减器）；50 Ω 输出用通用 `output_stage`（D50：同一行为不另造 `rf_output`）。除输出在电路层（戴维南源）外都是数据层 |
| 低频源（调制信号）单列 | ✅ `rf_mod_source`：调制频率在低频源上乘以时基误差（FM/PM 边带的位置），调制器用同一频率算 β = 频偏/频率；两者都绑定同一组 `am.rate`/`fm.rate`/`pm.rate`，改频率时由调制器做预算检查 |
| 隐藏状态只由测量发现；同步不在求解中读输入 | ✅ `rf_reference.sync()` 在信号变化时读 REF_IN 并缓存锁定状态（与旧整块相同的 invalidate 规则）；`error()` 只读缓存，避免求解电路时再次读输入 |
| `sb-sg-1` 定义与相关验收场景迁移 | ✅ 九个组件（reference/synth/mod_source/mod/level/att/rf/ref_out/ref_in）；隐藏状态路径改为 `reference.ppm`（`validation/benches/rfgen.bench.yaml`、`acceptance/benches/rfgen.bench.yaml`、`tests/test_internals.py` 同步）；金标准 2 项、`validation-rfgen.md` 的验收场景 34 项、`tests/test_model_components.py` 不改通过 |
| 射频源类别的块覆盖检查通过 | ✅ `test_block_coverage.py::test_the_validation_rf_generator_is_built_from_its_real_blocks`：参考、合成器、调制源、调制器、电平、衰减器、输出七块全覆盖；`docs/blocks/rf_generator.yaml` 增加调制源块，输出块接受通用 `output_stage` |
| 界面不坏：射频源的 Web 屏幕与 TUI 仪表屏幕 | ✅ 读数键改为组件限定：`synth.frequency/point/points`、`mod.lines/beta/omitted_power`、`reference.locked`；`RfGeneratorScreen` 与 i18n 的六个新组件类型同步；读数契约由 `tests/test_server.py` 的新用例守住（频率在合成器、谱线数与 β 在调制器、锁定在参考）。本地 e2e 42/42，另跑一次射频源屏幕冒烟（载波读数 1 GHz、谱线数 1、内部时基）；TUI 启动级 87 项与 24 快照通过（TUI 仪表屏幕按模板通用渲染，射频源没有专用读数键） |
| 视觉基线与本地结果 | ✅ Storybook 构建通过；`pnpm visual` 在 macOS 上按已知平台差异失败（Linux 基线），本 PR 未改组件库样式，不更新基线 |
| 缺口（记入清单） | ✅ 衰减器固定 0 dB（电平在全程精确，`validation-rfgen.md`）；脉冲/任意波调制不由首波块清单承担；型号 #197 现在可以开始 |
| `scripts/check.sh` 通过 | ✅ 1183 项 pytest 全绿 |

## D3：拆分锁相放大器为真实框图组件；补数据缓冲、AUX、偏置与输入配置（Issue 189，#181 的 D3）

| 验收标准 | 自检 |
| --- | --- |
| 第一步：重构前录金标准（消息、回复、虚拟时间），单独提交 | ✅ 首个提交 `95d4f51`：`tests/test_lockin_migration.py` + `tests/data/lockin_gold.json`，两条场景（RC 台的四种斜率 ENBW/建立、阶跃后的 n 阶指数建立、四个 Bode 点、同一虚拟时刻的噪声、灵敏度阶梯与 325/326、自动增益/相位；斩波台的外部参考与 1/3/5 次谐波、谐波上限拒绝、参考丢失 327 与恢复、截断方波 329、DC 背景下的 AC 耦合）。数值回复按 1e-9 比较、状态与错误文字逐字比较；拆分前在 monolith 上通过 |
| 按 component-map 框图拆分；每块定求解层级 | ✅ `lockin_input_filter`（AC 耦合与工频/二次工频陷波，数据层；输入阻抗仍在端子）、`lockin_input_amp`（灵敏度满量程、动态储备、输入限幅、噪声密度，数据层，读数是输入折算）、`lockin_mixer`（相敏检波，数据层）、`lockin_lowpass`（时间常数、斜率、闭式建立、329，数据层）、`lockin_output`（X/Y 偏置与扩展、R/θ、过载、自动功能、缓冲采样，数据层）、`lockin_reference`（现有）；端子与 AUX 在电路层 |
| 数据缓冲复用通用组件 | ✅ `sb-lia-1` 用 `trigger_model` + `reading_buffer`（#88），没有新做缓冲组件；输出组件实现 sampler 协议，采样点取自滤波器闭式解在该时刻的值（含该时刻的噪声），`sample_fields` 给 X/Y/R/θ/TIME |
| AUX 是电路层端口 | ✅ `lockin_aux`：4 路 ADC 输入（1 MΩ 对地）与 4 路 DAC 输出（1 kΩ 戴维南源），定义端口 `AUX_IN1..4`、`AUX_OUT1..4` 出现在测试台上；`:AUX:OUTPut n,v` 经定义的 `aux.output<n>` 状态设置，`:AUX:INPut? n` 读解出的输入电压 |
| 补偏置/扩展与输入配置 | ✅ X/Y 偏置（满量程百分比）与 ×1/×10/×100 扩展只作用于显示值，R/θ 保持原始；工频与二次工频陷波（Q = 10）、动态储备随灵敏度改变输入限幅（封顶 1.4 V 峰值）、耦合沿用 AC 0.16 Hz |
| `sb-lia-1` 与相关验收场景迁移 | ✅ 十二个组件（osc/out/sig_in/ref_in/front/amp/mixer/filter/demod/trig/buffer/aux）；`validation-lockin.md` 的验收场景 20 项、金标准 4 项、`tests/test_model_components.py` 不改通过；新增能力用例 `tests/test_lockin.py` 5 项 |
| 锁相类别的块覆盖检查通过 | ✅ `test_block_coverage.py::test_the_validation_lockin_is_built_from_its_real_blocks`：输入滤波、输入放大、混频、参考、低通、输出、缓冲、AUX 八块全覆盖；`docs/blocks/lockin.yaml` 注明缓冲复用通用组件 |
| 界面不坏：锁相 Web 屏幕与 TUI 仪表屏幕 | ✅ `LockinScreen` 增加“显示与缓冲”分组与储备/陷波设置，读数键（`demod.*`、`osc.*`）不变；本地 e2e 42/42 与一次锁相屏幕冒烟通过；TUI 启动级 87 项与 24 快照通过 |
| 视觉基线与本地结果 | ✅ Storybook 构建通过；`pnpm visual` 在 macOS 上按已知平台差异失败（Linux 基线），本 PR 未改组件库样式，不更新基线 |
| 缺口（记入清单） | ✅ 缓冲的暂停/续采（SR830 的 PAUS/REST）不在首波，触发模型按一次运行取满点数；AUX 的 DAC 精度与噪声未建模；SR830 的 IGND/ISRC 等接地与输入方式命令留给 #195 按手册核对（本模型单端输入，必要时另开能力 issue）；锁相的 1/f 噪声、真实 PLL 捕获时间与通信延迟仍不建模（validation-lockin.md 已写明） |
| `scripts/check.sh` 通过 | ✅ 1193 项 pytest 全绿 |

## F：频率计数器的输入调理、门控、时基与测量模式（Issue 192，#181 的 F）

| 验收标准 | 自检 |
| --- | --- |
| 输入调理按通道实例化（53220A 两个通道） | ✅ `counter_input`：引脚阻抗在电路层（1 MΩ / 50 Ω 设置，`electrical` 状态触发重解），耦合、阈值与灵敏度在数据层；`sb-counter-1` 的 `a`、`b` 两个实例接 CH1/CH2 |
| 门控组件：门时间、门控源（内部/外部） | ✅ `counter_gate`：门时间与门控源；真实门长 = 门时间 /（1 + 时基误差），外部源表示这一轮由触发模型开始 |
| 时基组件：内部/外部参考，复用 `reference_clock` | ✅ `sb-counter-1` 用 `reference_clock`（`ROSCillator:SOURce` 切换），时基误差按频谱仪同一约定缩放读数 |
| 测量模式：频率、周期、累加、时间间隔（两通道事件） | ✅ `counter_core`：频率/周期用门内事件的倒数计数（快时基读低 ε），累加数门内事件，时间间隔取 A 的首个事件到 B 的下一个事件并加两通道触发误差；SCPI `:MEASure:FREQuency/PERiod/TOTalize/TINTerval?`，门时间计入虚拟时间 |
| 统计：均值/最小/最大/标准差 | ✅ `counter_statistics`：`:CALCulate:STATistics:MEAN/MINimum/MAXimum/SDEViation/COUNt/CLEar`，样本标准差（n−1） |
| 粒度与命名按 D50 | ✅ 每个块是真实功能块（`counter_input`、`counter_gate`、`counter_core`、`counter_statistics`），参数参与仿真；阈值穿越按主谱线周期建模，多音/非谐波信号按最强谱线计数（模型边界，记入 component-map） |
| 计数器类别的默认面板 | ✅ `panels._counter`：按功能切换的主读数（频率/周期/累加/时间间隔）、统计均值/标准差/点数，测量模式按键、门控（门时间/门控源/每轮读数）与两路输入的耦合/阻抗/阈值/灵敏度；本地 e2e 冒烟通过 |
| 块覆盖检查通过 | ✅ `test_block_coverage.py::test_the_validation_counter_is_built_from_its_real_blocks`：输入、门控、时基、计数、统计、触发六块全覆盖 |
| 新组件有单元与场景测试 | ✅ `tests/test_counter.py` 9 项：频率/周期与门时间的虚拟时间、时基 2 ppm、累加计数、两通道时间间隔与触发误差、阈值/灵敏度拒绝、50 Ω 加载、AC 耦合去直流、统计、触发模型一次运行多读数 |
| 缺口（记入清单） | ✅ 53230A 的无间隙连续测量与时间戳不在首波；触发误差是模型值（1 ns，按虚拟时刻与种子确定），不是手册指标；非谐波信号按最强谱线计数（模型边界）；外部门控只表示触发源，门输入信号电平不建模 |
| `scripts/check.sh` 通过 | ✅ 1206 项 pytest 全绿 |

## SDS2104X Plus 的触发耦合、噪声抑制与释抑（Issue 159）

| 完成条件 | 自检 |
| --- | --- |
| 按 EN11F 的命令与取值 | ✅ 定义新增 `trigger.coupling`（DC / AC / LFREJect / HFREJect）、`trigger.nreject`（ON / OFF）、`trigger.holdoff_type`（OFF / TIME）与 `trigger.holdoff_time`（8 ns–30 s），命令 `:TRIGger:EDGE:COUPling`、`:TRIGger:EDGE:NREJect`、`:TRIGger:EDGE:HOLDoff`、`:TRIGger:EDGE:HLDTime`；覆盖表由未实现改为实现并写明：AC 高通 5 Hz（数据手册的交流耦合转折），LFREJ 高通 50 kHz、HFREJ 低通 1 MHz（数据手册未给，SimuBench 默认值），释抑类型与时间按 EN11F p.423–424（EVENts 未实现，设置它报错） |
| 接到已有的触发模块 | ✅ `scope_trigger` 的 `coupling` / `reject` / `filters` / `noise_reject` / `holdoff`；定义里用参数映射与状态绑定，没有改组件 |
| 手册用例 | ✅ `tests/models/siglent-sds2104xplus/cases.yaml` 新增“触发耦合、噪声抑制与释抑”：AC / LFREJect 回读、NREJ ON/OFF、HOLDoff 时间与格式，恢复 DC / OFF / 100 ns |
| 行为测试 | ✅ `tests/test_siglent_sds.py` 四项：耦合与抑制接到触发路径（DC 不变、LFREJ 把 1 kHz 压到 5 % 以下、HFREJ 通过）；释抑只在类型 TIME 时推迟下一次触发（OFF 不推迟，EN11F 的 EVENts 设置报错）；带直流偏置的信号在 DC 耦合下不触发、AC 耦合后在交流过零处触发；带噪声的慢边沿 NREJ OFF 时同一沿重复触发、ON 后只触发一次 |
| 检查 | ✅ `simubench validate` 无诊断；`simubench cases` 全部通过；`src/simubench/library/components/` 无改动；`scripts/check.sh` 全绿 |



## SDS 触发释抑语义与行为测试（Issue 250、251）

| 完成条件 | 自检 |
| --- | --- |
| HOLDoff 是释抑类型、HLDTime 是时间 | ✅ `:TRIGger:EDGE:HOLDoff {OFF|TIME}`（复位 OFF；EVENts 未实现，设置它按值域报错）与 `:TRIGger:EDGE:HLDTime`（8 ns–30 s，EN11F p.423–424）分别绑定 `trigger.holdoff_type` / `trigger.holdoff_time`；组件 `holdoff_on` 只在类型 TIME 时为真，OFF 时释抑不生效 |
| 手册用例 | ✅ `TRIG:EDGE:HOLD TIME` → `TIME`、`HLDT 1E-3` → `1.00E-03`、恢复 `HOLD OFF`；不再锁定“HOLDoff 收秒数”的错误行为 |
| 行为测试补齐 #159 的三项 | ✅ `tests/test_siglent_sds.py`：DC 偏置下 DC 耦合不触发、AC 耦合在交流过零触发；噪声慢边沿 NREJ OFF 重复触发、ON 只触发一次；释抑类型 OFF 不推迟、TIME 推迟 HLDTime |
| 检查 | ✅ 定义校验、手册用例、覆盖表一致性、`scripts/check.sh` 全绿；组件无改动 |


## 34465A CONFigure 的回复与默认值（Issue 262）

| 完成条件 | 自检 |
| --- | --- |
| `CONF?` 格式 | ✅ `"<功能> <量程>,<分辨率>"`（功能与量程间一个空格）：`meter.configure` 给出实际使用的量程（自动量程取当前档）与量程 × 当前 NPLC 分辨率，例如 `"VOLT +1.00000000E+01,+1.00000000E-06"`；手册用例按 p.240 的 Typical Return 改写 |
| CONFigure 恢复默认值 | ✅ `:CONFigure[:VOLTage]:{DC|AC}` 用 `writes` 恢复手册默认值表（量程自动、分辨率自动、NPLC 10、自动调零开、立即触发、触发/采样计数 1、自动触发延迟），再应用量程/分辨率参数；参数新增 `sets`，给量程即关闭自动量程。手册默认值表中平台没有的设置（如显示、格式）不在复位范围，覆盖表说明 |
| 现有用例与脚本 | ✅ 手册用例、`examples/scripts/keysight_34465a_measure.py` 的读数期望按 10 V 量程的 NPLC 分辨率更新；`tests/test_keysight_34465a.py` 通过 |
| 检查 | ✅ 定义校验、覆盖表一致性、`scripts/check.sh` 全绿 |


## 频率计数器：分辨率随门时间变化（Issue 255）

| 完成条件 | 自检 |
| --- | --- |
| 频率/周期的单次分辨率 | ✅ `counter_core` 新增 `single_shot_s`（默认 1 ns，模型值）；每个计数的端点事件加“内插器单次分辨率 ⊕ 通道触发误差”的正态误差，按虚拟时刻与种子确定，可复现；频率/周期的相对分辨率约 √2 × 单次分辨率 / 门时间 |
| 时间间隔的两个通道独立 | ✅ TINT 的开始/结束事件分别用通道 A/B 自己的误差抽取，不再只加一次；文档字符串同步 |
| 测试 | ✅ `tests/test_counter.py`：门时间 0.1 s 与 0.01 s 的 40 次重复读数标准差之比在 5–20（约 10）；频率重复测量的 `SDEV` 非零；原有精确断言按分辨率放宽到 1e-6 |
| 文档 | ✅ `docs/component-map.md` 的模型限制同步说明单次分辨率与门时间的关系 |
| 检查 | ✅ `scripts/check.sh` 全绿 |
