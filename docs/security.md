# 安全自检：本地服务

状态：每次 CI 由 `tests/test_security.py` 等测试复核。SimuBench 在工程师自己的电脑上运行，默认防护针对同一台电脑上的浏览器页面（跨站请求、DNS 重绑定）；本机其他用户只在开启访问令牌时防护（D43）。

| 防护 | 做法 | 测试 |
| --- | --- | --- |
| 只监听本机 | Web 服务与 SCPI 网络仪表（raw socket、HiSLIP 共用端口 D41，VXI-11 的核心、abort 与可选端口映射器 D63）只绑定 127.0.0.1 | `test_scpi_sockets_listen_on_loopback_only` |
| 连接模式控制端口（D63） | `serve`/`tui`/`mcp` 默认在 127.0.0.1:4881 监听（`--link-port`，`--no-scpi` 关闭），只绑定回环；与 raw socket、HiSLIP、VXI-11 一样没有登录（D43 的令牌只管 Web 接口）；只能调用 `Link` 的白名单方法，不接受别的操作；连接断开时锁释放；连着的脚本自己的等待会推进共享的虚拟时间（与它自己的命令一样），界面的显式推进仍要确认 | `test_scpi_sockets_listen_on_loopback_only`、`test_only_the_link_methods_can_be_called` |
| HiSLIP 加密与登录（可选，D41） | 有 `--hislip-cert`/`--hislip-key` 才开：TLS 1.2 起，加密强制或要求首先加密时未加密的消息是致命错误；加密后登录前只回应登录类消息；SASL PLAIN 凭证来自 `--hislip-users` 文件（常量时间比较，失败递增延迟；命令行只在 `--hislip-tls initial|mandatory` 时接受它），没有用户文件时只有 ANONYMOUS。**登录只管 HiSLIP 端口，不是测试台的访问控制**：加密可选时不请求加密的客户端与 1.0 客户端免登录，raw socket 与 VXI-11 没有登录。测试证书只在 `tests/data/hislip/` | `tests/test_hislip_tls.py` |
| 访问令牌（可选，默认关闭，D43） | `--require-token` 启动时随机生成（`secrets.token_urlsafe(24)`），或 `--token 值` 指定；界面地址中带一次后从地址栏移除；请求头 `x-simubench-token`，常量时间比较。开启后，除健康检查与接口描述（`/api/health`、`/api/openapi.json`、`/api/docs`，不含数据）外，每个 `/api` 路由都要求令牌。它是同机威胁模型下的一次性访问凭证，不用于登录或识别用户 | `test_every_api_route_needs_the_token`（遍历全部路由，新增路由自动纳入）、`test_cli_token_is_opt_in` |
| DNS 重绑定与跨站请求 | Host 与 Origin 头必须是本机地址（127.0.0.1、localhost、::1），否则 403，即使带了正确令牌 | `test_foreign_host_or_origin_is_refused_even_with_the_token`、`test_rebinding_host_rejected` |
| 事件流 WebSocket | 同样检查 Host、Origin，开启令牌时也检查令牌，失败时以 4403 / 4401 关闭 | `test_event_socket_checks_token_and_origin`、`test_without_a_token_by_default_but_still_loopback_only` |
| 工作区边界 | 测试台文件、版本历史、会话启动的路径都解析后必须位于工作区内（`..`、绝对路径拒绝，403）；运行记录编号只接受字母数字与连字符 | `test_bench_paths_stay_in_the_workspace`、`test_bench_path_cannot_escape_workspace`、`test_runs_api` |
| 诊断包 | 不包含访问令牌，不自动上传 | `test_diagnostics_bundle_over_the_api` |
| MCP 仿真控制 | 推进时钟、读取真实值默认关闭，需 `--allow-control`；读取真实值还需 Bench 中 `run.reveal_true_values`（验证模式下关闭，定标脚本读不到答案） | `tests/test_mcp.py` |
| 个体偏差 | 按指标抽取的值（D3 第 2 层）在会话接口中只在 `run.reveal_true_values` 打开时给出：仪表与 DUT 的 `deviations[].value` 为 null，DUT 的 `params` 显示标称值；MCP `read_true_values` 同样受这个开关与 `--allow-control` 限制。内部组件图只显示型号的标称值 | `tests/test_deviation.py::test_the_session_view_hides_drawn_values_unless_revealed`、`web/e2e/edit.spec.ts` |
| 内部探针 | 仪表内部端口的信号（`GET …/internals/probe`、MCP `probe_internal`）会显示个体值的效果：测试台不公开真实值（`run.reveal_true_values` 关闭）且该仪表带有隐藏的个体值（抽取的偏差、`hidden`、参数类故障、`set_parameter`；显式登记在 `Component.unit_values`，组件自身的运行时覆盖不算）时返回 403 / 工具拒绝，两边给出同一条含仪表名的原因；内部结构图照常返回。全部为标称值的仪表不受限 | `tests/test_internal_probe.py::test_validation_mode_withholds_an_instrument_with_hidden_values`、`::test_each_source_of_a_hidden_value_withholds_the_probe_unless_revealed`、`::test_a_blocks_own_run_time_overrides_hide_nothing` |
| 真值核对（C7） | `simubench verdicts` 与 MCP `check_verdicts` 在**运行结束后离线**核对定标脚本的判定：只读运行记录与脚本自己的报告，输出逐点结论与真误差。它不碰正在运行的会话——脚本运行中 `run.reveal_true_values` 关着时它什么也给不出，运行时的保密规则不变。MCP 工具与 `read_true_values` 一样需要 `--allow-control`：把真值交给 Agent 是工程师的显式行为 | `tests/test_verdicts.py`、`tests/test_mcp.py` |
| MCP 危险命令 | 打开输出、扫描等标记为危险的命令需要 `confirm=true`，Agent 必须先向工程师说明 | `tests/test_mcp.py` |
| MCP 频率限制 | 每个会话的命令速率受限，防止失控的 Agent 循环 | `tests/test_mcp.py` |
| 工作区插件代码 | `components/*.py` 默认**不**加载（不会被导入，更不会执行）；需 `--allow-plugins` 或工作区设置 `.simubench/settings.json` 的 `"allow_workspace_plugins": true` 显式开启（D3）。开启后加载了哪些插件会打印在启动横幅，`doctor` 与诊断包列出插件及来源（不含源码）；运行记录头部带每个插件文件的 sha256，回放不一致时告警（D36）。已安装包经 `simubench.components` 入口点提供的组件默认加载——安装包本身就是信任行为 | `tests/test_plugins.py`、`test_security.py::test_workspace_plugins_are_opt_in` |
| 文件写入 | 原子写入（临时文件 + 替换）；乐观并发（sha256），不会覆盖别人刚做的修改。**唯一的删除路径**是 AI 助手的“恢复到上一轮之前”（#876）：只删上一轮新建的测试台与工作区定义，由用户点按钮触发、先列出要删的文件，删除前核对 sha256（计划列出、确认后现读，停止会话后复核一遍——期间被改过就在任何写盘之前中止、要求再次确认；#908）；恢复前先核对所有要恢复的版本都在历史里，中途失败不留下半恢复状态（#900）；AI 工具没有删除文件的能力 | `tests/test_bench_api.py`、`tests/test_assistant_rollback.py`、`test_assistant.py::test_the_tools_cannot_delete_files_or_control_the_simulation` |
| AI 助手的 LLM（D66） | 请求由**服务端**发出，地址只允许 `http`/`https`、不得带用户名密码、不得指向本机 SimuBench 自己的端口（Web、SCPI、HiSLIP、VXI-11、连接模式；本机判定按解析后的真实地址，`127.1`、十进制/十六进制简写、尾点 `localhost.`、IPv4 映射的 IPv6 都算）；**保存时与每次使用前（发消息、连通性测试）都校验，环境变量与 `settings.json` 的默认地址同样适用（#901）**。**允许内网地址**是有意的取舍（私有化部署就在内网），不受 Host/Origin 检查影响。API key 只写不读：存工作区 `.simubench/assistant.json`（已 git-ignore，文件仅所有者可读写，Windows 尽力而为），任何接口返回、日志、诊断包都不带它，错误文本里出现时替换为 `***`。对话内容只发给用户自己配置的 LLM。工具权限在服务端强制：不删文件、不做仿真控制、不向运行中的测试台发 SCPI，运行中只调参数、停止要在用户回答后的下一轮 | `tests/test_assistant_llm.py`、`tests/test_assistant.py` |
| 仪表的文件（#1069） | MMEMory、:DISK 等命令读写的文件放在工作区 `.simubench/files/<测试台>/<仪表>/`（每台仪表一个文件夹，盘符为子文件夹，设置存取的内部寄存器在其 `_registers/` 下（#1070）；没有保存的测试台、手册用例与回放用会话自己的临时文件夹，关闭时删除）。脚本给的路径先按仪表的规则解析，`..`、仪表不允许的字符、未声明的盘符一律拒绝（回文件错误码），解析结果必须在该仪表的文件夹内，途经的符号链接拒绝且从不跟随；单台仪表合计不超过 256 MiB、单个文件不超过 64 MiB（定义可以更小）；写入原子（临时文件 + 替换）；文件只当数据读写，从不执行。API 只读（列出、下载），下载路径同样核对。运行记录头部记下运行开始时已有文件的 sha256，写入事件带 sha256，回放核对 | `tests/test_file_system.py`（路径逃逸、符号链接、超额、按仪表隔离、API 下载越界 403） |
| 依赖 | 锁定文件（`uv.lock`、`pnpm-lock.yaml`），CI 以锁定版本安装 | CI |

## 不在防护范围内

- 默认（未开启访问令牌）时，本机其他用户：他们可以直接访问 127.0.0.1 上的服务（Host/Origin 校验只针对浏览器），以启动者的身份读写工作区内的测试台、注入故障、发送 SCPI。在多人共用的电脑上请用 `--require-token` 启动。
- 本机拥有同一用户权限的恶意程序：它可以直接读取令牌所在的进程输出与文件。
- 把服务暴露到网络：不支持；需要远程访问时应在其前面放置有认证的反向代理（之后的私有化部署再考虑）。
- 被显式同意执行的代码：开启 `--allow-plugins`（或工作区设置）后，工作区 `components/*.py` 以启动者的用户权限执行任意代码，没有沙箱；已安装包的入口点组件同理——安装任何 Python 包本就可以在其安装与导入时执行代码。防护只保证**默认不执行**与**执行可追溯**（加载清单与文件哈希），不限制已同意代码能做什么。
- 抽取的个体偏差不是秘密数据，而是种子的确定函数：知道测试台（种子）与定义的人可以重算出来。开关防的是被验证的脚本经 SCPI、会话接口或 MCP 读到答案，不防有意重算。
- MCP 与 API 写不了 `.py`：文件写入只覆盖测试台（`*.bench.yaml`/`*.bench.json`）与定义（`instruments/<id>.yaml`，id 受字符白名单限制）。Agent 需要插件时，只能生成说明与草稿，由工程师亲手落盘（原则 3）。
