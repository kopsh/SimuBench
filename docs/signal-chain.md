# 信号链：产品设计与技术方案（第一版）

状态：**已实现（第一版）**。第 9 节的 S1–S11、S13–S15 已完成（S14 示波器分段存储 #957，S15 定义里的写法 #961）；S12 的真实型号前端指标已按数据手册填入（#960），其中平坦度的个体偏差与前置放大器选件等 #1004；实现与下文设计不同之处写在各节的“已实现 / 实现后的修正”框中，以框中为准。定义里怎样写信号链见 [instrument-authoring.md](instrument-authoring.md) 2.11（#961）。Web 的内部结构图与探针面板见 ui-design.md 的“内部结构图”。背景与策略见 [positioning-review.md](positioning-review.md) 第 7、8 节；结构性选择记录为 D67。
本文供实现的 agent 使用：第 1 节是产品设计，第 2–8 节是技术方案，第 9 节是 issue 拆分与验收。

## 0. 一句话

**对用户，第一版仍是仪表外部行为的仿真；对内，示波器与频谱仪的内部改成真实的信号链：模拟域保持解析（谱线），
ADC 之后是采样流，每个功能块的输出都有真实的中间信号、可以在界面中观察。** “把自己的算法放进去验证”的能力第一版不做，
但接口按它来留。

### 0.1 范围

| 做 | 不做（第一版） |
| --- | --- |
| 采样流：功能块之间的一种新连接 | 连续流（长时间不间断的采样流）；只做按帧请求 |
| 通用 ADC 块；示波器的采集经采样流实现，外部行为不变 | 示波器模拟前端的非线性 |
| 频谱仪功能块之间传递信号；前端非理想（平坦度、相位噪声、交调、杂散） | 零扫宽、IQ 输出、解调、通道功率类测量 |
| 采样式（FFT）频谱仪求值，与解析求值互为对照 | 采样式求值下的相位噪声与非线性（解析求值下有） |
| 内部探针：内部结构图上任一端口的时域与频谱（Web、API、MCP） | TUI 中的内部探针 |
| 示波器：脉宽触发、分段存储 | 码型、串行、视频触发；数学通道与 FFT（留给后续） |
| | **算法托管、定点数据类型、金标准比对、链路预算工具**（等与算法工程师确认需求之后） |

### 0.2 不变量

实现的每一步都必须保持：

1. **确定**：同一测试台、同一种子、同一动作序列，全部回复逐字节相同（D6、D12）；同一虚拟时刻两次读取相同。
2. **现有回复不变**：除本文明确列为“行为变化”的条目外，现有测试、用例（`tests/models/*/cases.yaml`）、验收场景的回复不变。
   每个重构 issue 先录金标准再动代码（#94 方法，`tests/helpers.py` 的金标准表）。
3. **拒绝而不猜**：超出模型能力时报仪表定义的错误码并标注保真度，不返回近似值冒充精确值。
4. **没有装饰性的块**（D50）：新增的每个功能块都带参与仿真的参数或行为，并且有输出信号。
5. **被动读取不改变仿真**（D55）：内部探针经 `Session.view()`，不推进虚拟时间、不进运行记录、不消耗脚本的随机数流。

---

## 1. 产品设计

### 1.1 用户与任务

| 用户 | 任务 | 接触到的界面 |
| --- | --- | --- |
| 写脚本/上位机的工程师 | 现有程序连上虚拟仪表，行为与真机一致 | SCPI、仪表屏幕、通信记录（现有）。不需要知道内部结构 |
| 想确认“这台虚拟仪表靠不靠谱”的仪表工程师 | 打开内部结构，看它是不是按真实仪表的方式工作 | 内部结构图 + 内部探针（本文） |
| 我们自己（演示） | 改一个内部参数，外部行为随之变化 | 测试台的个体偏差（`hidden`）+ 内部探针 |

### 1.2 内部结构图（改进现有页面，`web/src/features/workbench/internals/`）

现有页面已显示组件、信号连线、引用（`refs`）、参数与驱动它的命令（#20、#739）。改动：

1. **按信号流向排布**：从输入端口到结果，自左向右；信号连线是主线，引用画成次要的虚线。
   示波器每个通道一行，共享块（ADC 组、时基、触发）在行的交汇处。
2. **连线区分两种信号**，文字 + 线型，不只靠颜色（roadmap.md 界面质量标准）：
   - 模拟（解析）：标“模拟”；
   - 采样流：标“数字”，并在连线上显示采样率与格式，如 `2 GSa/s · 8 位`、`50 MSa/s · IQ`；线型为空心双线（#953）。
3. **每个输出端口可点开内部探针**（1.3）。
4. **功能块卡片**：块的名称用仪表工程师的说法（“输入衰减器”“混频器”“抽取与采集方式”）；参数带单位；
   参数来自数据手册的，显示来源（定义中参数旁的注释已是目录说明，沿用）。
5. **页首说明**（固定文案，中英文）：“这是功能框图级的模型：功能块的划分依据公开资料（见依据），不是该型号的实际电路设计。”
   “依据”显示定义的 `reference`（#953：标题 · 文档号 · 版本，经 `InstrumentInternals.reference`）；component-map.md 中该类别登记的来源是文档，不在界面里链接。

### 1.3 内部探针

点开任一功能块的输出端口，侧栏显示该点的信号。与现有的网络探针（D37）同一套视觉与组件。

| 区域 | 模拟（解析）端口 | 采样流端口 |
| --- | --- | --- |
| 标题 | `混频器 · 中频输出` · 模拟 | `ADC · 输出` · 数字 · 50 MSa/s · 16 位 |
| 时域 | 波形（按解析式求值） | 采样点（阶梯或点，不插值） |
| 频域 | 谱线表与谱线图 | 该帧的 FFT 幅度谱，纵轴 dBFS（码值）或 dBm/dBV |
| 数值 | 直流、有效值、峰值 | 同左；另有码值范围占用（如“用到满量程的 37%”）、是否削波 |
| 说明 | 保真度标注（截断、近似） | 同左 |
| 为 0 的原因 | 沿用 `engine/silence.py`（上游无信号、输出关闭……） | 同左 |

- **频谱仪的调谐**：混频器之后的信号取决于本振频率。探针面板有“调谐于”一项，默认是中心频率，可改为任一标记的频率。
  实现（#959）：仪表声明了调谐上下文（`InstrumentInternals.tuning`）、且所看的端口在混频器之后时才显示；一个输入框始终显示当前调谐点，可直接输入（带单位，范围为仪表的频率范围，超出时就地说明、调谐点不变）；
  旁边是“中心频率”（跟随设置）与每个已开启的标记（跟随标记）。调谐点在探针换端口时保留，可沿着链一路看同一个频率。
  IQ 流与频点的谱图横轴是射频频率：点谱图上的一点（或最强峰列表里的按钮、键盘 Enter）即把调谐点移到那里。
- **谱图的读法**（#959）：`bins` 帧直接画幅度谱，不再做 FFT，也不画时域；`iq` 帧时域画 I、Q 两条（同一刻度，线型与文字区分），频域画双边谱。
  谱图上有读数光标（指针或左右键，读所在柱内最强的频点）与噪声底（各频点电平的中位数）。
  模拟端口的谱线在远离 0 Hz 处扎堆时（1 GHz 处相距 1 MHz 的两个单音；混频器 25 MHz 中频上的产物与 2 GHz 的和频产物并存），
  谱线图取最强谱线所在的一组为横轴，注明图外还有几条，可切回“从 0 Hz 起看全部”；谱线表给出相对最强谱线的电平（dBc）。
- **刷新**：随事件版本刷新（D64），不轮询；会话在运行时看到的是当前时刻的信号。
- **不提供**：在探针里修改参数、导出数据、把块替换成别的实现。这些属于被推后的能力。

### 1.4 外部行为的变化（用户可见）

| 仪表 | 变化 | 何时出现 |
| --- | --- | --- |
| 频谱仪 | 迹线上出现相位噪声裙边、二次/三阶失真产物、镜像与杂散、随频率起伏的幅度 | 只在定义声明了对应参数时；`sb-sa-1` 不声明，回复不变 |
| 频谱仪 | 输入过强时不只是报过载，增益压缩体现在读数上 | 同上 |
| 示波器 | 脉宽触发；分段存储 | 定义声明并映射到命令后 |
| 示波器 | 无变化（采集改经采样流实现是内部重构），下一行除外 | — |
| 示波器 | 平均采集的 `次数 × 每次采集的采样数` 超过 2²⁸（`MAX_REQUEST_FANOUT`）时，采集在开始前被拒绝：按仪表自己的错误码（DSO-X、UXR 为 −221），不产生记录，随后的测量与读取同样立刻被拒绝；运行中的屏幕保持已有的记录。重构前这样的设置会一直算下去（10 万点 × 8192 次约 70 秒，× 65536 次十分钟以上），命令返回前会话不响应——上限以内的设置逐位不变（#986） | DSO-X 2024A：10 万点记录时次数 > 2683；SDS2104X Plus：2 万点及以下全部档位可用，10 万点时 4096、8192 被拒绝；UXR0334B：`次数 × 点数 > 2²⁸` |

覆盖表的说明随之更新（原先写“未建模”的条目改为“实现”）。

### 1.5 演示脚本（验收用，写成 e2e 用例）

1. 打开频谱仪测试台（信号源输出两个相距 1 MHz 的单音），内部结构图自左向右显示：
   射频输入 → 输入衰减器 → 混频器 ← 本振 ← 参考；混频器 → 中频滤波 → ADC → 数字下变频 → RBW 滤波/FFT → 检波 → 视频滤波 → 迹线。
2. 点“混频器 · 中频输出”：谱线图上能看到两个单音搬到中频，以及三阶交调产物。
3. 点“ADC · 输出”：采样点与 FFT 谱；标题显示采样率与位数。
4. 在测试台里把 ADC 位数从 16 改为 12（个体偏差）：ADC 输出的噪声底抬高；在采样式求值的仪表上，迹线的噪声底同步抬高。
5. 示波器测试台：点 ADC 输出看到量化台阶；切到峰值检测，点“抽取”输出，窄脉冲被保留。

### 1.6 文案与术语（加入 glossary.md）

| 中文 | English | 说明 |
| --- | --- | --- |
| 信号链 | Signal chain | 仪表内部从输入端口到结果的功能块序列 |
| 采样流 | Sample stream | 功能块之间传递的采样数据（帧），带采样率与格式 |
| 内部探针 | Internal probe | 观察仪表内部某个端口的信号；不影响仿真 |
| 模拟 / 数字 | Analog / Digital | 连线上的信号是解析描述还是采样流 |
| 求值方式 | Evaluation | 解析（谱线）或采样；频谱仪定义可选 |

---

## 2. 架构

### 2.1 两个域，一个边界

```
            模拟域（现有）                         数字域（新增）
  medium: electrical / signal                    medium: samples
  数据：Signal（直流 + 谱线，解析）              数据：SampleFrame（一帧采样，带采样率与格式）
  求值：output(port) -> Signal                   求值：frames(port, request) -> SampleFrame

  源 → 线缆 → DUT → 输入端口 → 衰减 → 放大 → 混频 → 滤波 ──▶ [ADC] ──▶ 下变频 → 抽取 → FFT → 检波 → 迹线
                                                              ↑
                              唯一的边界：按采样时刻对 Signal 求值，加入模拟噪声的实现、量化、削波
```

- 模拟域不变：电路求解、谱线、传递函数、能力上限（architecture.md 1.4、2）。
- 数字域是**按帧拉取**的：下游向上游要“从时刻 t0 起、间隔 dt 的 N 个采样”，上游返回一帧。没有后台线程、没有推送、没有流的隐含状态。
- ADC 是唯一把 `Signal` 变成 `SampleFrame` 的地方。DAC（反方向）第一版不做。

### 2.2 为什么按帧拉取、块不带流状态

真实硬件里采样流是连续的，滤波器有历史。仿真里如果让块记住“上一帧的尾巴”，结果就取决于谁在什么时候读过，
违反“同一时刻两次读取相同”与被动读取不改变仿真（0.2）。因此：

- **一帧是请求的纯函数**：`frames(port, request)` 的结果只取决于请求、块的参数、上游信号的当前版本与种子。
- **需要历史的块声明预热长度**：FIR/CIC/FFT 窗等向上游多要 `warmup` 个采样并丢弃对应的输出，而不是保存状态。
- **需要多次采集的处理（平均）**由请求中的子序号区分，每次是独立的一帧。
- 代价：不能表达跨帧的无限冲激响应的精确尾巴（用足够长的预热近似，块在说明中写明长度与误差界）；不支持连续流。

### 2.3 与现有机制的关系

| 现有 | 关系 |
| --- | --- |
| `Component.output(port) -> Signal`、`Component.input`（经 `owner.wiring.source` 与 `session.cached_output`，#895） | 不变。新增平行的一对 `frames` / `input_frames` |
| `refs`（按名字引用兄弟组件） | 保留给“设置依赖”（时基、触发读取谁）。**信号一律走连线**：本文把现在靠 `refs` 取数据的地方改成连线 |
| 宏展开（`library/macros.py`） | 旧写法的定义在加载时展开成新结构，定义文件不必改；展开结果与显式写出的指纹相同（instrument-authoring.md 8.1） |
| 个体偏差（测试台的 `hidden`，组件参数覆盖） | 不变；新块的参数自动可覆盖，演示脚本第 4 步靠它 |
| 计数器式随机数（`engine/noise.py`） | 不变；采样流中的噪声同样按（种子、组件、流、样本序号）取值 |
| 插件出口（`plugin_api: 1`） | 不变；新增的钩子是追加的，插件组件可以实现 `frames`。版本号不升 |
| 被动视图（D55） | 内部探针经它读取 |

---

## 3. 采样流

### 3.1 数据类型（新文件 `src/simubench/engine/stream.py`）

```python
@dataclass(frozen=True)
class SampleFormat:
    kind: Literal["volts", "codes", "iq", "bins"]   # 实数电压 / ADC 码值 / 复基带 / 频点上的复幅度（5.3）
    bits: int = 0                           # codes：位数；0 表示未量化
    lsb: float = 0.0                        # codes：一个码对应的输入电压，V
    signed: bool = True

@dataclass(frozen=True)
class FrameRequest:
    t0_ns: int                 # 第 0 个采样的时刻（虚拟时间）
    count: int                 # 采样点数
    dt_s: float                # 采样间隔（请求方认为的标称值）
    key: tuple[object, ...]    # 随机数流的键：由发起请求的块给出（自己的 uid、流名、采集序号、子序号、显示帧号……）
    index0: int = 0            # 第 0 个采样在该随机数流中的序号（平均的第 k 次、绝对采样网格上的位置）
    tune_hz: float | None = None   # 频谱仪：本次请求的本振调谐点；其他仪表为 None
    first: int = 0             # 第 0 个采样在以 t0_ns 为锚点的网格上的序号：采样 k 的时刻是 t0_ns + (first + k) * dt_s。
                               # 触发落在两个采样之间时，整数纳秒放不准记录的起点，整数序号乘浮点间隔可以（#950）

@dataclass(frozen=True)
class SampleFrame:
    data: np.ndarray           # float64（volts/codes，codes 以整数值的浮点保存）或 complex128（iq）
    t0_ns: int
    dt_s: float
    fmt: SampleFormat
    center_hz: float = 0.0     # iq：这帧的 0 Hz 对应的射频/中频频率
    df_hz: float = 0.0         # bins：频点间隔
    clipped: bool = False      # 本帧是否有采样触到满量程
    notes: tuple[FidelityNote, ...] = ()
```

- 码值以浮点数组保存整数值：与现有 `scope_adc.quantize`（“the codes, as volts”）一致，避免在第一版引入定点类型。
  定点数据类型属于被推后的能力，届时扩展 `SampleFormat`，不改帧的结构。
- **上限**：`MAX_FRAME = 1 << 26` 点（单帧）、`MAX_REQUEST_FANOUT = 1 << 28`（一次读取沿链展开的总采样数）。
  起初定为 2²²、2²⁶，实现示波器时改大：10 万点记录的峰值检测一帧就是 640 万个原生采样，现有行为不能被上限拒绝（#950）。
  超出时抛 `ModelLimit`（沿用现有“超出模型能力”事件与定义的错误码）。这两个数是可调常量，写在 `stream.py` 顶部并在 architecture.md 2.3 的上限表中登记。

### 3.2 组件接口（`engine/component.py`）

```python
Medium = Literal["signal", "electrical", "reference", "samples"]

class Component:
    def frames(self, port: str, request: FrameRequest) -> SampleFrame:
        """A frame leaving a samples output port. Override in components with sample outputs."""
        raise NotImplementedError

    def input_frames(self, port: str, request: FrameRequest) -> SampleFrame:
        """The frame arriving at a samples input port (the upstream block's frames)."""
        src = self.owner.wiring.source(self.name, port)   # engine/wiring.py (SignalWiring, #895)
        return self.session.cached_frames(self.owner.components[src[0]], src[1], request)
```

- 上游的解析与 `Component.input` 相同：`SignalWiring.source` 给出上游端口；采样流输入不允许悬空，校验期拒绝。
- 新钩子（`frames`、`input_frames`、`sample_interval`）纳入 `tests/test_component_catalog.py` 的钩子签名契约（#898、#929）。
- `Session.cached_frames(comp, port, request)`：缓存键 `(信号版本, comp.uid, port, request)`，容量小（64 帧，LRU），
  只为一次读取内的重复请求与探针服务；信号版本变化即失效。
- **定义校验**（`engine/validate_definition.py`）新增：
  - `E-DEF-WIRE-MEDIUM`（已有同类检查则扩展）：`samples` 输出只能接 `samples` 输入；`signal` 输出不能接 `samples` 输入——中间必须有 ADC；
  - `E-DEF-SAMPLES-OPEN`：`samples` 输入没有连线；`samples` 端口也不能映射为仪表端口（测试台的连线不承载采样流）；
  - 带 `samples` 输出端口的组件类必须覆盖 `frames`（在 `tests/test_component_catalog.py` 的类元数据检查中加，不在运行时发现）。
- **速率变换**：块在 `frames` 中用 `request.upstream(M, warmup)` 得到向上游的请求：`count * M + warmup` 点、间隔 `dt / M`、
  起始时刻提前 `warmup` 个采样、随机数流的序号换到细网格上（同一时刻取同一个噪声值）。
  请求沿链向上展开的总量由 `Session` 计数并对照 `MAX_REQUEST_FANOUT`。
  一次读取里向上游发多次请求的块（示波器平均：每次子采集一帧）先用 `Session.reserve_frames(comp, port, 总采样数)`
  判断，超出时在第一帧之前拒绝，不先算一部分再报错（#986）。
- **噪声键**：上游模拟链的噪声在 ADC 处实现时用 `stream_key(seed, *request.key)`，样本序号为 `request.index0 + k`；
  块自身新增的噪声源（如 ADC 的 `noise_lsb_rms`）在其后追加 `(self.uid, <流名>)`。发起请求的块决定 `request.key` 与 `index0`，
  因此脚本的采集、屏幕显示的帧、内部探针各用各的键，互不干扰（现有 `panel_frame()` 的做法，#746）；
  示波器的采集沿用现在的键，重构前后逐位相同（4.2）。

### 3.3 通用 ADC（`library/components/adc.py`，现有量化内核所在的文件）

> **实现后的修正（#950）**：`scope_adc.frames` 已实现，与下文有三处不同，频谱仪的 `adc`（#955）照此办理。
> （1）请求里的 `dt_s` 是**实际**采样间隔，由发起请求的块（它持有时钟）除过时钟误差；ADC 不再除一次——
> 两处各除一次与一处除完再分，浮点结果差一个末位，金标准不过。（2）帧里是“量化后换回电压的码值”
> （`SampleFormat("volts", bits, lsb)`），不是原始码值：现有的平均、峰值检测都在这个量上做，先平均再换算不逐位相同。
> （3）没有建 `SamplingAdc` 基类：两种 ADC 共用的只有模块级的量化内核 `adc.codes`，等 #955 写第二个 ADC 时再看是否值得抽基类。

新增基类 `SamplingAdc(Component)`，两个组件类型继承它：

| 类型 | 用途 | 满量程的来源 |
| --- | --- | --- |
| `scope_adc`（已有，扩展） | 示波器的 ADC 组：多路输入、通道配对的最高采样率、每格码值 | 垂直通道的档位与偏置（现有 `AcquisitionInput` 协议） |
| `adc`（新） | 单路、固定满量程：频谱仪的中频 ADC、以后的数字化仪 | 参数 `full_scale`（V，峰值） |

端口：`in`（`signal`，输入）、`out`（`samples`，输出）；`scope_adc` 为每通道一对 `in<n>` / `out<n>`。

`frames(out, request)` 的步骤（顺序固定，写进类的说明）：

1. **采样时刻**：`request.t0_ns + k * dt_actual`，`dt_actual = request.dt_s / (1 + 时钟误差)`；时钟误差来自引用的时钟块
   （示波器是时基，频谱仪是参考时钟）。返回的帧保持标称 `dt_s`（现有 G3 的约定）。可选孔径抖动 `aperture_jitter_s`（默认 0）。
2. **求值**：`self.input("in").sample_rel(t0, offsets)`（现有逐点采样）。
3. **模拟噪声的实现**：上游模拟链的噪声在这里变成采样。`scope_adc` 取所属通道垂直块的 `input_noise_rms()`（现有，D46）；
   `adc` 取连线上游的级联噪声（5.2 的 `RfStage`）在奈奎斯特带宽内的有效值。
4. **ADC 自身**：`noise_lsb_rms`（默认 0）；量化与削波用文件里已有的内核，位数为 0 时不量化。
5. 返回 `SampleFrame(fmt=codes, bits, lsb, clipped=...)`。

参数（每个带目录说明的注释）：`bits`、`full_scale`（仅 `adc`）、`max_rate`、`aperture_jitter_s`、`noise_lsb_rms`、`clock`（引用）。
有效位数随频率下降、交织失配属于后续（7.2 的缺口表保留这两项）。

---

## 4. 示波器：采集经采样流实现（A1）

### 4.1 目标结构

```
每通道： input_port ─▶ scope_vertical ─▶ scope_probe ─┐(signal)
                                                      ▼
共享：                                             scope_adc ──▶ scope_decimator ──▶ scope_acquisition ──▶ measurements / waveform_transfer
                                                  (samples，原生速率) (samples，记录速率)   （存储：Record）
触发：scope_vertical 的输出 ─▶ scope_trigger（模拟通路，现有；不变）
引用：scope_timebase、scope_trigger 仍由 acquisition 引用（它们是设置与时序，不是数据）
```

### 4.2 改动

| 现在（`scope.py` `_acquire`） | 改为 |
| --- | --- |
| 采集组件自己 `sample_rel`、加噪声、调用 `adc.quantize` | `scope_adc.frames` 完成（3.3） |
| 峰值检测、平均、高分辨率写在 `_acquire` 的分支里 | 新组件 `scope_decimator`：`in<n>`（samples，原生速率）→ `out<n>`（samples，记录速率）；参数 `processing`、`averages`、`eres_bits`、`peak_max_oversample` 从采集组件移过来 |
| 采集组件按名字读各模块 | 采集组件只做：定时（`_timing`）、触发搜索、组织 `FrameRequest`、把帧装成 `Record`、运行控制与待完成操作 |
| `vertical.correct(volts)`（校准修正）在 `_acquire` 末尾 | 留在采集组件装 `Record` 时（它是显示侧的修正，位置不变） |

要点：

- **噪声序号按 ADC 的原生网格计**（实现后的修正）：采集组件向抽取块请求时，`index0` 已经是原生网格上的序号
  （`time` 键：`floor(t_start / native_dt)`），平均的第 k 次由抽取块加 `k × 原生点数`；不使用通用的 `upstream()` 换算。
  采集组件为此向抽取块询问过采样倍数（`oversample()`，一个设置量）。
- **噪声键不变**：采集组件把现在传给 `stream_key` 的全部参数原样放进 `request.key`（采集组件自己的 uid、`"noise"` 或 `"frame"`、通道号，
  以及采集序号，或 `time` 模式下的 `dt` 与 `noise_stream`，或显示帧号），把现在的样本序号起点放进 `request.index0`
  （`sub * count`，或 `floor(t_start / dt) + sub * count`）。`scope_adc` 按 3.2 的规则取数，每个采样的噪声值与重构前逐位相同。这是金标准不变的前提。
- **平均**：`scope_decimator` 对子序号 0..N−1 各请求一帧量化后的采样再取均值（与现状同序）。
- **峰值检测**：请求 `points × m` 个原生速率采样，按现有规则在每个间隔取极值。
- **触发**取 ADC 输入端的模拟信号（`scope_adc.analog(n)`），与原来读探头输出是同一个信号。
- **行为变化两处**（PR #950 列出）：高分辨率（ERES/HRES）的平滑原来少返回一个点（N 点得 N−1 点，窗口偏一个采样），已修正，
  金标准中 DSO-X 与 SDS 的这两行随之重录；屏幕帧在平均方式下，原来各次子采集用同一份噪声，现在各用各的。
- **宏**：`macros.py` 的 `scope_acquisition` 宏扩展——定义没有显式写 `scope_decimator` 时生成一个（名字 `dec`），
  把 `processing` 等参数从采集移到它，并补上三段连线 `probe.out → adc.in<n> → dec.in<n> → acq.in<n>`。内置与验证定义**不改文件**。
- `docs/blocks/oscilloscope.yaml` 增加一块 `decimator`（抽取与采集方式），component-map.md 的示波器表同步。

### 4.3 验收

1. 先提交金标准：`tests/test_scope_stream_migration.py`，用 `tests/helpers.py` 的金标准表跑 `sb-dso-4`、`sb-dso-2x`、
   DSO-X 2024A、SDS2104X Plus、UXR0334B、WavePro 804HD 各自的示例脚本与一组覆盖四种处理方式 × 触发/未触发 × 平均的读取，记录回复的哈希。
2. 重构后该文件与全部现有 `tests/test_scope_*.py`、各型号 `cases.yaml`、`acceptance/` 不改一个预期值即通过。
3. 新测试 `tests/test_stream.py`：帧是请求的纯函数（同请求两次相同、不同 `key` 不同）；`signal → samples` 无 ADC 的连线被校验拒绝；超出 `MAX_FRAME` 报模型上限。
4. 性能：`tests/test_performance.py` 中示波器采集的预算不放宽。

---

## 5. 频谱仪：功能块之间传递信号（A2、A4）

### 5.1 目标结构

```
rf_in(input_port) ─▶ specan_input ─▶ specan_mixer ─▶ specan_if ─▶ adc ─▶ specan_ddc ─▶ specan_rbw ─▶ specan_detector ─▶ specan_vbw ─▶ specan_trace
     electrical        衰减/前放       ▲ 本振          中频滤波     ADC    数字下变频     RBW/FFT        检波             视频滤波       迹线、标记、扫描
                                   specan_lo ◀── reference_clock
        └──────────────── 模拟（signal）────────────────┘└──────────────── 数字（samples）─────────────────┘
```

新增组件类型：`specan_lo`（本振：调谐、相位噪声、步进/扫描速率）、`specan_ddc`、`specan_rbw`；`adc` 来自 3.3。
`specan_if` 现在同时承担“RBW”与“噪声底”，拆开：模拟的中频（抗混叠）滤波留在 `specan_if`，RBW 归 `specan_rbw`，噪声归各级的噪声系数（5.2）。
旧参数名经宏展开兼容；`mixer_limit`、`maxhold_limit` 借此按 D50 更名（component-map.md 的命名债务）。

### 5.2 模拟前端：信号与级联指标

**信号**：每个模拟块覆盖 `output("out")`，返回 `Signal`（谱线）：

| 块 | `output` | 声明参数（都来自数据手册；默认值使行为与现状相同） |
| --- | --- | --- |
| `specan_input` | 输入谱线 × 衰减 × 平坦度 H(f) | `att`/`att_auto`（现有）、`preamp_gain`、`flatness`: `[[频率, dB], …]`（分段线性，默认空） |
| `specan_mixer` | 在调谐点 `tune_hz` 下，输入谱线搬到中频：`f_if = f_rf − f_lo`；另加镜像响应、本振馈通、失真产物（见下） | `conversion_loss`、`image_rejection`（dB，默认无穷大）、`lo_feedthrough`（dBm，默认无）、`spurs`: `[[m, n, dBc], …]` |
| `specan_if` | × 中频滤波器 H(f)、× 中频增益 | `if_hz`、`bandwidth`、`gain` |

- **调谐上下文**：混频器及其下游的输出取决于本振频率。`output` 读取 `self.session` 提供的当前调谐点（`specan_lo.tuned()`，
  由迹线引擎在求值每个分段前设置，探针按面板的“调谐于”设置）。调谐点属于求值的输入，进入 `cached_output` 的键。
- **已实现（#952）**：`with session.tuned(f): …`，可重入；块内读 `self.session.tune_hz`（`None` 表示不在上下文里，此时
  `specan_lo.tuned()` 取中心频率）。调谐点指“接收机调到的频率”（显示频率轴上），本振频率是
  `specan_lo.frequency() = (调谐点 − if_hz) × (1 + 参考误差)`，`if_hz` 在 `specan_if` 上。读调谐点的端口由组件类的 `tuned_ports`
  声明，定义校验拒绝把它们映射为仪表端口（`E-DEF-PORT-TUNED`）。解析求值不把谱线在中频来回搬一遍（浮点往返不是恒等）：
  迹线沿连线从 `specan_if` 向上游取**链首的谱线**与各级的 `stage(f)`，显示按输入校准；未声明非理想时回复逐位不变。
- **非线性产物在谱线级生成**：由级联的二阶、三阶截点，对输入的谱线两两（三阶再加自身）组合，生成 `2f₁`、`f₁±f₂`、`2f₁−f₂` 等频率上的新谱线，
  幅度按截点公式；只取最强的 `MAX_PRODUCT_LINES = 64` 条输入线参与组合，被略去的在 `notes` 中标注“近似”。增益压缩按 P1dB 的标准三阶模型作用于各线。
- **相位噪声**不是谱线：`specan_lo` 声明 `phase_noise: [[偏移 Hz, dBc/Hz], …]`（对数-对数插值）。解析求值时，迹线引擎对每个输出点加上
  `Σ 线功率 × L(|f − f线|) × 噪声带宽`，按**噪声**对待（进检波器的噪声统计，而不是当作一条线）。

**级联指标**（新协议 `RfStage`，`engine/component.py`，与 `Sampler` 等并列）：

```python
class RfStage(Protocol):
    def stage(self, f_hz: float) -> RfStageSpec: ...
# RfStageSpec: gain_db, noise_figure_db, iip2_dbm, iip3_dbm, p1db_dbm（没有的为 None / inf）
```

`specan_input`、`specan_mixer`、`specan_if` 实现它。级联（弗里斯公式的噪声系数、截点的级联）算一次、放在 `engine/rfcascade.py`，
作为纯函数（可单测）。迹线引擎与 `adc` 都从“沿连线向上游走到输入端口”得到的级列表计算，**不**各写一份。
相加方式（#974）：噪声系数按弗里斯公式；**三阶截点与 P1dB 按功率（mW）倒数相加**，1/P = Σ Gₖ/Pₖ（Gₖ 为该级之前
的增益，dB）——三阶产物折算到链输入时电压增益以平方出现，即功率形式；**二阶截点按幅度（电压）倒数相加**，
1/A = Σ αₖ/Aₖ（αₖ 为该级之前的电压增益，同一连续阻抗下与功率增益同一个 dB 值）——二阶产物折算到链输入时电压
增益只以一次方出现，两者不是同一个公式：两个等同级各 +30 dBm 的 IIP2 级联得 +24.0 dBm（少 6.02 dB，电压减半），
而同样的 IIP3 得 +6.99 dBm（少 3.01 dB）。
现有的 `noise_figure`（在 `specan_mixer` 上）作为整机噪声系数的旧写法保留：只声明它、不声明各级时，级联结果恒等于它，回复不变。

### 5.3 数字后端

| 块 | `frames` | 参数 |
| --- | --- | --- |
| `adc` | 3.3；输入是 `specan_if` 的输出，满量程固定 | `bits`、`full_scale`、`max_rate` |
| `specan_ddc` | 码值 → 乘 NCO（把中频搬到 0）→ 抽取滤波 → `iq` 帧；`center_hz` 记下对应的射频频率 | `decimation`（自动：由 RBW 决定）、`taps`/滤波器类型 |
| `specan_rbw` | 加窗 FFT：窗的等效噪声带宽 = RBW；输出每个频点的复幅度 | `window`（`gaussian`/`flattop`/`kaiser`…，默认使形状与现有高斯响应一致）、`steps`、`rbw_min`/`rbw_max`、`auto_ratio`（从 `specan_if` 移来） |
| `specan_detector` | 把每个显示点所辖的频点（桶）按检波方式合成（现有逻辑），再取对数 | 现有 |
| `specan_vbw` | 现有 | 现有 |

`specan_rbw` 输出的是“频点上的复幅度”，不是时间采样；用同一个 `SampleFrame`，`fmt.kind = "iq"`、`dt_s` 记频点间隔的倒数关系不直观——
因此为它新增 `kind: "bins"`（`data` 为复幅度，`center_hz` 为中心，新增字段 `df_hz` 为频点间隔）。`SampleFormat.kind` 的取值相应为
`volts | codes | iq | bins`。探针对 `bins` 直接画谱。

### 5.4 两种求值方式（`specan_trace.evaluation`）

| | `analytic`（默认，现有） | `sampled`（新增） |
| --- | --- | --- |
| 迹线怎么来 | 谱线经 RBW 响应解析地画到每个点 + 按分辨单元抽取的噪声（现有 `_sweep`） | 把跨度切成分段，每段：设调谐点 → 向 `specan_rbw` 请求一帧 → 检波 → 拼接 |
| 用到的块 | 模拟前端的 `output` + 级联指标 + RBW 的响应函数 | 整条链的 `frames` |
| 相位噪声、失真、杂散、平坦度 | 有（5.2） | 平坦度、镜像、杂散有（它们已在模拟输出的谱线里）；相位噪声与逐点非线性**没有**，覆盖说明中写明 |
| 代价 | 与设置无关，快 | 与 `跨度 / RBW` 成正比；超出 `MAX_REQUEST_FANOUT` 时报定义的错误码（拒绝而不猜） |
| 谁用 | 全部内置真实型号与 `sb-sa-1`：回复不变（未声明新参数时） | 新的验证仪表 `sb-sa-fft-1`；以后的自研数字中频仪表 |

为什么保留两种：解析求值没有代价上限问题，现有型号的外部行为不能因为窄 RBW 宽跨度而变成“拒绝”；采样式求值是信号链真正被用来产生回复的证明，
也是自研仪表（数字中频）将来要用的方式。**两者共用同一批功能块与同一份参数**，不是两套实现：模拟块的 `output` 两边都用，
`specan_rbw` 的窗同时给出解析求值用的响应函数（窗的频响）与采样求值用的窗系数。

无论哪种求值方式，内部探针对采样流端口都走 `frames`（一帧 4096 点的小请求），所以解析求值的仪表同样能点开 ADC 与下变频的输出。

> **已实现（#955）**，与上文不同或补充之处：
> （1）**RBW 的定义**：`specan_rbw` 的窗是高斯窗，RBW 是它的 −3 dB 宽度（与现有解析响应一致），所以等效噪声带宽是 1.0645 × RBW，
> 不是 RBW 本身。只实现了 `gaussian` 一种窗（第 8 节第 5 条：flattop、kaiser 没有仪表用到，不预留）。
> （2）**速率由请求决定**：`specan_ddc` 的抽取比默认自动——它按被请求的速率运行（ADC 采样率的整数分之一）；`specan_rbw` 请求的速率是
> “一次变换覆盖 `fft_width`（64）个 RBW”。`decimation`、滤波器类型不在迹线里另算一遍。滤波器只有凯泽窗 sinc 一种，
> 阶数由 `stopband`（默认 80 dB）决定：11 个输出间隔长，预热为长度减一个 ADC 采样。
> （3）**调谐点随请求走**：`FrameRequest.tune_hz` 由发起读取的迹线或探针填写，`adc` 在求值输入信号时进入 `Session.tuned`；
> 它是帧缓存键的一部分（此前探针的请求不带它，两个调谐点会读到同一帧）。
> （4）**时钟误差**：请求的 `dt_s` 是实际间隔（标称 ÷ (1 + ε)），NCO 按“每个 ADC 采样多少周”计，所以数字中频的频率轴与扫频式一样只带一次参考误差。
> （5）**分段与上限**：每段取变换中间的 `min(输出速率的一半, 中频通带的 60 %)`；分段数 × 每段沿链的采样数由 `StreamStage.upstream_request`
> 逐块求出，超出 `MAX_REQUEST_FANOUT` 或 `MAX_SWEEP_BINS`（2²² 个频点）时**在计算之前**拒绝。每次扫描单独计数。
> （6）**历史**：采样链只能读 RF_IN 当前的信号，所以采样式求值在输入变化时让迹线重新开始（保持与平均不跨越变化）；解析求值仍按历史逐次扫描求值。
> （7）**`specan_if`**：通带是 5 阶最平坦幅度（`bandwidth` 为 −3 dB 宽度），不再是 RBW 的高斯通带；`if_filter.out` 的探针读数随之改变（SCPI 回复不变）。
> （8）**`adc`**：参数为 `bits`、`full_scale`、`rate`、`noise_lsb_rms`、`impedance`；没有 `aperture_jitter_s` 与 `clock`（时钟误差随请求来，孔径抖动没有仪表用到）；
> 没有抽 `SamplingAdc` 基类——与 `scope_adc` 共用的仍只有量化内核 `adc.codes`。

### 5.5 验收

1. 金标准：`tests/test_specan_migration.py` 已有；补上 N9320B、N9042B 示例脚本的回复哈希。拆分与改连线后全部不变。
2. `tests/test_rfcascade.py`：级联噪声系数、截点对照手算（三级的教科书例子）；只声明整机噪声系数时等于它。
3. 前端非理想（各一条测试，用测试专用定义声明参数）：
   - 两个 −10 dBm 单音、三阶截点 +10 dBm：`2f₁−f₂` 处读到 −50 dBm（±0.1 dB）；
   - 相位噪声 −100 dBc/Hz@10 kHz、RBW 1 kHz：载波旁 10 kHz 处的平均迹线为 载波 −70 dB（噪声带宽修正后 ±0.5 dB）；
   - 平坦度表在某频率 −1 dB：该频率的单音读数低 1 dB；
   - 镜像抑制 60 dB：`f_lo + f_if` 侧的单音在调谐于 `f_lo − f_if` 时读到 −60 dBc。
4. **两种求值互为对照**（`tests/test_specan_sampled.py`，`sb-sa-fft-1` 与同参数的解析定义）：单音的峰值幅度差 ≤ 0.05 dB、频率差 ≤ 一个点；
   平均 100 次的噪声底差 ≤ 0.3 dB；RBW 改变 10 倍，噪声底变化 10 dB ± 0.3 dB；ADC 位数 16 → 12，采样式的噪声底按量化噪声公式抬高（±0.5 dB）。
5. 真实型号的参数（可认领）：N9320B、N9042B 从数据手册填平坦度、相位噪声、截点、位移平均噪声电平；覆盖表更新；每个型号加 3–5 条用例。

---

## 6. 内部探针（技术）

### 6.1 接口

- 服务：`src/simubench/server/services/internal_probe.py`（Web 路由与 MCP 工具共用，#894 的做法），读取经 `Session.view()`。
- HTTP：`GET /api/sessions/{sid}/instruments/{name}/internals/probe?port=<组件>.<端口>&tune_hz=<可选>` → `M.InternalProbe`。
- MCP：`probe_internal(session, instrument, port, tune_hz=None)`，返回同一形状；不需要 `--allow-control`（只读，且不含个体偏差的抽取值）。
- 模型（`models/api.py`，随后 `simubench schemas` 与 `pnpm gen:api`）：

```python
class InternalProbe(_Model):
    port: str                       # 'adc.out'
    medium: Literal["signal", "samples"]
    t_ns: int
    dc: float; rms: float; peak: float
    waveform: NetWaveform           # 复用网络探针的波形模型；采样流为采样点（iq 为同相分量）
    quadrature: list[float] | None  # 仅 iq：正交分量（#959）
    lines: list[SpectralLine] = []  # 仅 signal
    spectrum: ProbeSpectrum | None  # 仅 samples：freq_hz[]、level[]、unit('dBFS'|'dBm'|'dBV')
    sample_rate_hz: float | None
    format: ProbeFormat | None      # kind、bits
    full_scale_used: float | None   # 码值：峰值占满量程的比例
    clipped: bool = False
    tune_hz: float | None           # 读取时的调谐点（未指定时为中心频率）；端口不随调谐变化时为空
    notes: list[str] = []
    silence: list[SilenceReason] = []
```

### 6.2 规则

- 采样流端口：发出 `FrameRequest(t0_ns=now, count=4096, dt_s=该端口的当前采样间隔, key=("probe",), index0=该时刻在采样网格上的序号)`。
  噪声序号按时刻计（不用面板帧计数）：同一时刻两次探针给出同一帧，探针不推进任何计数器。
  端口的当前采样间隔由块给出（钩子 `Component.sample_interval(port)`，带 `samples` 输出的块必须实现）。
  没有自身速率的转换器（“问多快就采多快”，如 `sb-dso-4` 的理想 ADC）按读它的块所要的间隔取：钩子 `Component.input_interval(port)`（`scope_decimator` 已实现）。
- **保密**：探针显示的是信号，信号由个体偏差决定。测试台不公开真实值（`run.reveal_true_values` 关闭，验证模式，security.md）
  且**该仪表带有隐藏的个体值**（抽取的个体偏差、测试台的 `hidden`、参数类故障——都落在组件的 `overrides` 里）时，内部探针接口返回 403，
  界面显示“验证模式下不提供内部探针”——否则定标脚本的作者可以从 ADC 码值反推被隐藏的增益误差。内部结构图（标称参数）照常可看。
  仪表全部为标称值时没有可泄露的内容，探针照常提供（实现 #951 时的修正：`reveal_true_values` 默认关闭，按原先“关闭即拒绝”的写法，
  所有自带示例测试台上都用不了内部探针）。
- **调谐上下文**（#959）：`InstrumentInternals.tuning`（`InternalTuning`：`center_hz`、`min_hz`/`max_hz`、`marks`（已开启的标记及增量标记的参考）、`ports`（随调谐变化的输出端口：声明了 `tuned_ports` 的块的输出及其下游全部输出））。
  由块的两个钩子给出：`Component.tuning()`（`specan_lo`：中心频率及其所绑定设置的范围）、`Component.tune_marks()`（`specan_trace`）。没有块读调谐点的仪表为空。
  探针对声明了调谐上下文的仪表总按一个明确的调谐点读（未指定时为中心频率），因此 `iq`、`bins` 帧的频率轴总是调谐点附近的射频频率；`tune_hz` 超出范围时返回 400。
- `InstrumentInternals`（现有）给每个端口补 `medium`，给连线补 `medium` 与采样流的 `sample_rate_hz`、`format`，供 1.2 的标注使用。
- 性能预算：一次探针请求 ≤ 50 ms（4 通道示波器、频谱仪全链）。

### 6.3 Web

`InternalsView.tsx`：排布改为按信号流（1.2）；端口可点击、可键盘聚焦；探针面板复用网络探针的波形与谱线组件，新增采样流的谱图。
文案进 i18n（中文为主、英文为辅）。Storybook 增加两个故事（示波器、频谱仪），e2e 增加 1.5 的演示脚本，视觉回归增加两张基准图。
频谱仪的排布（#959，`layout.ts`）：没有通道行的仪表，最长的信号路径排成一行，其余在其下方；只被末端块使用的各级（检波、视频滤波）按末端块列出它们的顺序接在信号行之后；
被使用的块若自己又由更靠左的块供给（本振之于参考时钟），站在使用者（混频器）的正下方，依赖线竖直向上；列间距按最宽的连线标注取。
频谱仪演示用的测试台是 `web/e2e/fixtures/benches/sa-two-tone.bench.yaml`（两台信号源经电阻式功分器合路；混频器的三阶截点、噪声系数与 ADC 位数是该个体的 `hidden`）。
按 `web-ui` 技能的规则执行。

---

## 7. 示波器的数字功能（A5 的一部分）

在 A1 之后做，都以 DSO-X 2024A 为基准型号（D45），命令与行为按它的手册：

| 功能 | 做法 | 验收 |
| --- | --- | --- |
| 脉宽触发 | `scope_trigger` 增加触发类型 `glitch`（宽度小于/大于/范围）；在模拟触发通路的过零时刻序列上判定（现有搜索已得到亚采样的过零时刻），不新建采样流通路 | 一串 1 ms 周期的脉冲中有一个 50 µs 的窄脉冲：`< 100 µs` 条件只触发在它上；条件不满足时 NORMal 等待、AUTO 超时 |
| 分段存储 | `scope_acquisition` 增加 `segments`：连续 N 次触发各存一条记录，段间死区按声明的重新布防时间；段的时间戳可读 | 每段的触发时刻差等于信号周期的整数倍且不小于重新布防时间；读第 k 段得到第 k 条记录 |

码型、串行、视频触发与数学通道/FFT 不在第一版。

---

## 8. 为“验证算法与架构”留的接口（只约束，不实现）

实现时遵守下列各条，使以后的算法托管是纯增量：

1. 数字处理块的全部行为在 `frames(port, request)` 里；不从 `refs` 取数据，不读兄弟块的私有成员。
2. 块的输出只取决于请求、参数、上游帧与种子（2.2）。
3. 帧的格式字段齐全（采样率、类型、位数、中心频率）：块之间的约定写在数据里，不靠口头约定。
4. 每个数字处理块的核心算法是模块级的纯函数（如 `ddc(samples, f_nco, decimation, taps) -> iq`），块只是包装——
   与 `measurements.py` 的做法一致，可以脱离会话单测，也是以后与金标准比对的入口。
5. 不为第一版没有的能力预留参数、开关或空实现。

---

## 9. 实现拆分

全部是能力建设，标签 `核心`，除注明 `可认领` 的。每个 issue 一个 PR；PR 标题按 AGENTS.md（用户可见的变化；纯重构写“内部”）。
总 issue 建一个，下列各项为子 issue。

| 编号 | 内容 | 依赖 | 完成条件 |
| --- | --- | --- | --- |
| S1 | 采样流基础：`engine/stream.py`、`samples` 介质、`frames`/`input_frames`/`cached_frames`、定义校验、上限；architecture.md 第 2 节与 glossary.md 更新；`component` 技能补“求解层级：采样流” | — | 3.1、3.2；`tests/test_stream.py`（用测试内的小组件）；schemas 重新生成 |
| S2 | 示波器金标准：`tests/test_scope_stream_migration.py` | — | 4.3 第 1 条；只加测试，不改实现 |
| S3 | `SamplingAdc` 基类与 `scope_adc.frames`；`scope_decimator`；采集组件改经采样流；宏展开；blocks/component-map 更新 | S1、S2 | 4.3 第 2–4 条 |
| S4 | 内部探针：服务、HTTP、MCP、`InternalProbe` 模型、`InstrumentInternals` 的介质标注、验证模式下拒绝 | S1 | 6.1、6.2；`tests/test_internal_probe.py`：模拟端口与采样流端口各一、被动（时间与记录不变）、验证模式 403、预算 |
| S5 | Web 内部结构图与探针面板 | S4 | 1.2、1.3、6.3；e2e 演示脚本中示波器的步骤 |
| S6 | 频谱仪金标准补全（N9320B、N9042B 示例脚本） | — | 5.5 第 1 条 |
| S7 | `RfStage` 与 `engine/rfcascade.py` | — | 5.5 第 2 条 |
| S8 | 频谱仪模拟前端传递信号：`specan_input`/`specan_mixer`/`specan_if` 的 `output`、`specan_lo`、调谐上下文、连线与宏；更名（`mixer_limit` 等） | S6、S7 | 回复不变（5.5 第 1 条）；探针能看到混频器输出的谱线 |
| S9 | 前端非理想（解析求值）：平坦度、镜像、馈通与杂散、截点与压缩、相位噪声 | S8 | 5.5 第 3 条；未声明参数时回复不变 |
| S10 | 数字后端：通用 `adc`、`specan_ddc`、`specan_rbw`（含 `bins` 帧）、检波对接；`specan_trace.evaluation: sampled`；`sb-sa-fft-1` | S1、S8 | 5.5 第 4 条 |
| S11 | Web：频谱仪的探针（调谐于、谱图）、演示脚本的频谱仪步骤、视觉基准 | S5、S10 | 1.5 全部步骤作为 e2e 通过 |
| S12 | `可认领`：N9320B、N9042B 按数据手册填前端参数，覆盖表与用例 | S9 | 5.5 第 5 条 |
| S13 | 示波器脉宽触发（DSO-X 2024A） | S3 | 第 7 节 |
| S14 | 示波器分段存储（DSO-X 2024A） | S3 | 第 7 节 |
| S15 | `可认领`：文档收尾——instrument-authoring.md 增加“信号链”一节（怎样在定义里写采样流的块与连线）、real-models.md 注明两种求值方式的覆盖说明写法 | S10 | 文档中的示例由 `tests/test_docs_examples.py` 运行 |

可并行：S1、S2、S6、S7 同时开始；S3 与 S4 在 S1 之后并行；S8 在 S6、S7 之后；S13、S14 在 S3 之后任意时刻。

### 9.1 每个 PR 的通用要求

- `scripts/check.sh` 通过；全量测试不超过 60 s（AGENTS.md）。新测试遵守 D49（单元 ≤ 100 ms、不用正则）。
- 改了组件：同步 `docs/blocks/<class>.yaml`、component-map.md 的表、`tests/test_block_coverage.py` 与 `test_model_components.py` 的例外清单（只减不增）。
- 改了定义格式或接口模型：`uv run simubench schemas --out schemas`、`cd web && pnpm gen:api`，一并提交。
- 行为变化（1.4）在 PR 描述里逐条列出；不属于 1.4 的回复变化一律视为缺陷。
- 在 `docs/progress-log.md` 末尾追加自检一节。

### 9.2 风险与对策

| 风险 | 对策 |
| --- | --- |
| 示波器重构改变了噪声的逐位取值 | 4.2 的噪声键规则；S2 先于 S3 合并 |
| 采样式频谱仪在窄 RBW 宽跨度下算不动 | 只用于声明 `evaluation: sampled` 的定义；有上限并拒绝；现有型号不受影响 |
| 调谐上下文让 `output` 不再只是信号版本的函数 | 调谐点进入缓存键；只有混频器及其下游读取它；校验拒绝把这些端口接到仪表外部 |
| 做成没人用的通用框架 | 第 8 节第 5 条；每个新块都必须被 `sb-sa-fft-1` 或内置型号实际使用（D50，块覆盖测试） |
| 内部探针泄露被隐藏的个体偏差 | 6.2 的验证模式规则，带测试 |
