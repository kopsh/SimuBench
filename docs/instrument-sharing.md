# 仪表共享服务（设计稿）

状态：草案，待与产品负责人对齐。分工：本稿是设计（`核心`）；对齐后由设计方在 decisions.md 记 D66（第 9 节）、
把 scope.md 第 8 节"测试台与 DUT 资产库、共享、复用"移出"以后再说"，再按第 10 节的工作包开 `可认领` issue，
由其他人实施。实施者只按本稿与 issue 的验收标准做；本稿没写到、需要新判断的地方，开 `核心` issue 回到设计，
不在实施 issue 里自行决定（AGENTS.md）。

## 0. 目标

工程师在自己的工作区里建的仪表（`instruments/<id>.yaml`，含 `my-<id>` 复制件，#843）可以发布出去，
同事在仪表库里看到、装进自己的工作区、在测试台里使用。仪表库从此分三类：

| 来源 | 在哪 | 能否编辑 | 标识 | 例 |
| --- | --- | --- | --- | --- |
| **内置** | 随包发布的 `library/instruments/`（D42） | 只读 | 裸 id | `keysight-34465a` |
| **本地自建** | 工作区 `instruments/` | 可编辑，有版本历史（D35） | 裸 id | `my-keysight-34465a`、`acme-dmm` |
| **其他用户创建** | 共享服务；装到工作区后是锁定版本的只读副本 | 只读；要改先"复制为我的仪表" | `@<发布者>/<id>` | `@alice/acme-dmm` |

不变的前提：

- **本地优先**（product-form.md 2）：仿真、SCPI、CI 全在本机跑；共享服务只在"找、装、发布、升级"时用到，
  **运行时绝不联网**。断网、内网 CI、回放（D36）都只依赖工作区里的文件。
- **测试台与定义文件是契约**：装进来的共享仪表是工作区里可读、可 diff、可进 Git 的文件，不是黑盒缓存。
- **不分发代码**：共享的只有声明式定义（YAML + 覆盖表 + 用例），不含 `components/*.py` 插件（security.md、D3）。

## 1. 现状（main @ #843）

- `Library(search_dirs)` 按 工作区 `instruments/` → `SIMUBENCH_DEFINITIONS` → 内置 的顺序按 id 查找，
  先找到的胜出；同名时工作区遮住内置。
- `DefinitionSummary.workspace: bool` 只区分两类；首页"我的"标签由它而来。
- `DefinitionFiles.copy_of` 写 `my-<id>.yaml`（只含 `extends: <id>`），`extends` 在
  工作区目录、环境变量目录、内置目录中查父定义（D59）。
- 定义 id 白名单 `[A-Za-z0-9][A-Za-z0-9._-]*`；测试台 `instruments[].model` 是裸 id。
- 没有用户身份：D43 的令牌是同机一次性凭证，不识别人。

缺口：id 只在一个工作区内唯一（两个人都有 `my-keysight-34465a`）、没有版本概念、没有身份。

## 2. 总体结构

```
 工程师 A 的电脑                         内网                         工程师 B 的电脑
┌──────────────────────┐   发布(定义包)   ┌────────────────────┐   搜索/安装   ┌──────────────────────┐
│ simubench serve      │ ──────────────▶ │ simubench hub      │ ◀──────────── │ simubench serve      │
│  instruments/        │                 │  元数据库 + 包存储  │               │  instruments/        │
│   acme-dmm.yaml      │                 │  服务端校验         │               │   @alice/acme-dmm…   │
└──────────────────────┘                 └────────────────────┘               │  shared.lock         │
                                                                               └──────────────────────┘
```

- **`simubench hub`**：同一个 Python 包里的新子命令（FastAPI，同一套 pydantic 模型生成 OpenAPI，D21），
  私有化部署在内网；元数据存 SQLite（单机）或 PostgreSQL，定义包按内容哈希存文件系统或对象存储。
- **本地 `serve` / MCP / CLI 是它的客户端**：浏览器只跟本机服务说话，由本机服务转发到 hub——
  本机服务的 Host/Origin 规则（security.md）不变，浏览器不需要跨域访问 hub。
- 不做"把引擎搬上 hub"：hub 不跑仿真，只做存取与静态校验。

## 3. 标识与命名空间

- 共享仪表的全名是 `@<发布者>/<id>`；`<发布者>` 是用户名或团队名（第 6 节），`<id>` 沿用现有白名单。
  `@` 开头的名字只能来自共享，内置与本地自建永远是裸 id，三者在任何工作区里都不会撞名。
- 测试台 `model:` 接受 `@alice/acme-dmm`；`extends:` 也接受（"复制为我的仪表"作用于共享仪表时写
  `extends: "@alice/acme-dmm"`）。
- 发布时 id 由作者给：本地 `acme-dmm` 发布成 `@alice/acme-dmm`；本地的 `my-` 前缀建议在发布对话框里去掉。
- 查找顺序变为：裸 id → 工作区 `instruments/` → 环境变量目录 → 内置（不变）；`@x/y` → 工作区
  `instruments/@x/y.yaml`（只此一处，找不到就报"未安装，运行 `simubench shared install @x/y`"，不联网补）。

## 4. 版本与锁定

- 发布即产生**不可变版本**：版本号是递增整数（`v1, v2…`），另记内容 sha256；同一版本永不覆盖。
  可以"撤回"（yank）：已锁定它的工作区仍能用，但新安装看不到、界面提示。
- 工作区安装后得到：
  - `instruments/@alice/acme-dmm.yaml`（+ `.coverage.yaml`、`cases.yaml` 若有），文件头部写来源注释；
  - `shared.lock`（工作区根，YAML）：每个已装仪表的 版本、sha256、hub 地址、安装时间。
- 运行与校验只看本地文件；加载时比对 sha256，文件被手改则报错（"共享仪表是只读的，要改请复制为我的仪表"）。
- 升级是显式动作（界面"有新版本 v3"→ 查看变更 → 升级），变更说明复用 `definition_changes`（T3）。
  升级记入版本历史（D35），测试台的运行记录头部已有定义哈希（D36），升级前后的运行可区分。
- 建议 `instruments/@*/` 与 `shared.lock` 跟测试台一起进 Git：CI 机器不需要访问 hub。

## 5. 发布：一个"定义包"里有什么

| 内容 | 必须 | 说明 |
| --- | --- | --- |
| 定义 YAML | 是 | 发布前把 `extends` 链上**本地自建**的祖先展平进来（D59 的合并结果）；指向内置或共享的 `extends` 保留为依赖 |
| 覆盖表、用例 | 带 `reference` 时是 | D42：带依据的定义适用同一套规则，hub 也检查三者一致 |
| `reference` 元数据 | 带就带 | 只有 title/document/version；手册文件不上传（版权，#412） |
| 需要的引擎版本 | 自动 | 发布者的 SimuBench 版本（D58）与用到的组件类型；装到旧版本时提示而不是静默出错 |
| 变更说明 | 否 | 一行，给升级对话框看 |

发布时拒绝：用到工作区插件组件类型的定义（不分发代码）；`extends` 指向未发布的本地定义且无法展平；
校验（`validate_definition_data`、覆盖表、用例）有 error。hub 端**再跑一遍**同样的静态校验，不信任客户端。

内置仪表依赖：依赖 `extends: keysight-34465a` 的共享仪表在不同 SimuBench 版本上合并结果可能不同。
包里记发布时父定义的哈希；安装端版本不同时提示"父型号已变化，请用用例复核"（`simubench cases`），不阻止。

## 6. 身份、可见性、权限

- 内部阶段（D17）：hub 接公司 SSO（OIDC）或放在有认证的反向代理后面；hub 自己不存密码。
- 本机客户端用**个人访问令牌**连 hub：`simubench shared login` 把令牌存在用户配置目录
  （`~/.config/simubench/hub.json`，不进工作区、不进诊断包、不进运行记录）。这与 D43 的本机令牌是两回事。
- 可见性三档：`private`（只有自己，跨电脑同步用）/ `team`（团队成员）/ `org`（全公司）。
  **默认 `team`**：首批用户处理的是未发布型号的命令集（product-form.md 1），不应默认全公司可见。
- 发布者命名空间：个人 `@alice`，团队 `@acme-dmm-team`；团队成员都能发布新版本，只有发布者/团队管理员能撤回、转移。
- 审计：谁在何时发布、安装、撤回哪个版本，hub 留日志（运维数据，与用户资产分开，D17）。

## 7. 接口

### 7.1 hub（`/v1`）

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/v1/definitions?q=&class=&owner=` | 搜索：名称、厂商、型号、类别、发布者、最新版本、下载数 |
| GET | `/v1/definitions/{owner}/{id}` | 详情与版本列表（含撤回标记、变更说明） |
| GET | `/v1/definitions/{owner}/{id}/versions/{n}` | 版本元数据 + sha256 |
| GET | `/v1/definitions/{owner}/{id}/versions/{n}/bundle` | 定义包（tar.gz，按 sha256 缓存） |
| POST | `/v1/definitions/{owner}/{id}/versions` | 发布新版本（服务端校验，失败返回与本地相同的 Diagnostic 列表） |
| POST | `/v1/definitions/{owner}/{id}/versions/{n}/yank` | 撤回 |
| PATCH | `/v1/definitions/{owner}/{id}` | 可见性、描述、转移 |

### 7.2 本机服务（新增，均在 `/api` 下，经现有令牌与 Host/Origin 规则）

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/shared/search` | 转发搜索，并标出本工作区已装版本 |
| POST | `/api/shared/install` | `{name, version?}`：下载、校验 sha256、写文件与 `shared.lock`、记版本历史 |
| POST | `/api/shared/{owner}/{id}/upgrade` | 升级到指定版本，返回变更 |
| DELETE | `/api/shared/{owner}/{id}` | 卸载（有测试台引用时拒绝并列出） |
| POST | `/api/definitions/{id}/publish` | 发布本地自建定义：`{as_id, owner, visibility, note}` |

`DefinitionSummary` 的 `workspace: bool` 换成 `source: "builtin" | "local" | "shared"`，共享的另带
`shared: {owner, version, latest, yanked}`；按 #843 的做法保留一个版本的 `workspace` 兼容字段后删除。
MCP 增加 `search_shared`、`install_shared`（安装要 `confirm=true`，它写工作区文件）；
发布**不**给 MCP——把定义交给别人是工程师的显式行为（与 `read_true_values` 同理）。
CLI：`simubench shared login | search | install | upgrade | publish | list`。

## 8. 界面

原型：[prototypes/instrument-sharing.html](prototypes/instrument-sharing.html)（静态页，浏览器直接打开；
颜色取自 `web/src/styles/globals.css`，黄色便签是给实施者的说明）。截图：

| 屏 | 内容 | 截图 |
| --- | --- | --- |
| P1 | 首页仪表库：来源分段（全部/我的/共享/内置）+ 类别标签；三组卡片与徽标（我的、共享、有新版本、已撤回）；「导入定义包」「在共享库中搜索」 | [p1](prototypes/instrument-sharing/p1.png) |
| P2 | 共享库抽屉：搜索、发布者/类别筛选、行内按钮随本地状态变；安装对话框列出要写入的文件、父型号变化提示 | [p2](prototypes/instrument-sharing/p2.png) |
| P3 | 发布对话框：发布到（自己/团队）、名称、可见性三档（默认团队）、变更说明、发布前检查（✕ 禁用发布，本地祖先展平提示） | [p3](prototypes/instrument-sharing/p3.png) |
| P4 | 共享仪表验收页：头部只读标识、版本、来源；新版本横幅；「历史」标签换成「版本」；复制为我的仪表、卸载 | [p4](prototypes/instrument-sharing/p4.png) |
| P5 | 升级对话框：`definition_changes` 变更列表、受影响测试台、升级后跑用例 | [p5](prototypes/instrument-sharing/p5.png) |
| P6 | 工作台：元件库三分组；测试台引用未安装的共享仪表时节点画成缺失、问题面板给 `E-SHARED-NOT-INSTALLED` 与「安装」 | [p6](prototypes/instrument-sharing/p6.png) |
| P7 | 设置 · 共享库：地址、登录状态（已连接/未登录/连不上/未配置） | [p7](prototypes/instrument-sharing/p7.png) |

规则（原型之外也成立）：

- 三组顺序处处一致：**我的 → 共享 → 内置**；空组不显示。共享徽标优先级 已撤回 > 有新版本 > 共享。
- 没配置 hub 时共享相关入口（搜索、发布、版本标签的 hub 列表）不出现，不报错；「导入定义包」始终可用（W3）。
- hub 连不上只影响搜索、安装、升级、发布；已安装的共享仪表照常使用、验收、试用。
- 共享仪表只读：编辑入口一律换成「复制为我的仪表」，复制件是 `extends: "@owner/id"`。
- 文字终稿进 `web/src/i18n/zh.ts`、`en.ts`；与 TUI 共用的措辞按 web-ui / tui-ui 技能。TUI 第一期只在仪表列表显示来源，不做安装与发布。
- 截图由 Playwright 在 1280 px 下按 `section.screen` 截取；改原型后重截。

## 9. 需要记入 decisions.md 的选择（对齐后由设计方写成一条 D66）

1. **装到工作区的副本 + 锁文件**，而不是用户级缓存目录：备选"`~/.cache` 里按版本缓存、工作区只写锁文件"
   省磁盘、多个工作区共享，但 CI 与离线机器必须能访问 hub，测试台也不再自包含；不采用。
2. **`@发布者/id` 命名空间**，而不是全局唯一裸 id：裸 id 要求全公司抢名字，而且与内置型号将来的 id 冲突。
3. **不可变整数版本**，而不是 semver：定义没有"API 兼容"的清晰边界，升级一律看变更列表 + 跑用例。
4. **只共享声明式定义，不共享插件代码**：共享代码就要签名、沙箱或审核流程；需要时另立决策。
5. **默认可见性 team**。

## 10. 精确格式（实施以此为准）

### 10.1 `shared.lock`（工作区根）

```yaml
schema: simubench.shared-lock/1
hub: https://simubench-hub.example.internal   # 安装时的 hub；为空表示从本地包文件导入（W3）
definitions:
  "@alice/acme-dmm":
    version: 3
    sha256: 9f2c…            # 定义 YAML 原始字节的 sha256（不是包的）
    files:                   # 安装写下的全部文件及各自 sha256，卸载与校验都按这张表
      instruments/@alice/acme-dmm.yaml: 9f2c…
      instruments/@alice/acme-dmm.coverage.yaml: 51ab…
    requires: {simubench: "0.42.0", parents: {keysight-34465a: 7c1d…}}
    installed_at: 2026-11-09T12:00:00Z
```

- 键按名字排序、`safe_dump`，保证 Git diff 稳定；模型放 `models/shared.py`，进 `schemas/`（生成）。
- 用例文件装到 `instruments/@alice/acme-dmm.cases.yaml`（不放 `tests/models/`，那是内置型号的位置），
  `DefinitionFiles` 找用例时多查这一处。

### 10.2 定义包（`.sbdef`，即 tar.gz）

```
manifest.json      {"schema":"simubench.definition-bundle/1","name":"@alice/acme-dmm","version":3,
                    "note":"…","published_by":"alice","published_at":"…","simubench":"0.42.0",
                    "component_types":["dmm_frontend",…],"parents":{"keysight-34465a":"7c1d…"},
                    "files":{"definition.yaml":"9f2c…","coverage.yaml":"51ab…","cases.yaml":"…"}}
definition.yaml    展平本地祖先后的定义，`id` 写成 `@alice/acme-dmm`
coverage.yaml      可选
cases.yaml         可选
```

- 解包只接受这四个文件名，拒绝其他条目、链接、绝对路径与 `..`（沿用工作区边界规则）；单包上限 2 MB。
- `version` 由 hub 分配；W3 的本地导出包由客户端写 `version: 0`、`published_by` 为空。

### 10.3 id 与路径

- 共享名正则：`@[a-z0-9][a-z0-9-]{0,38}/[A-Za-z0-9][A-Za-z0-9._-]*`；文件路径
  `instruments/@<owner>/<id>.yaml`。`DefinitionFiles.rel_path` 对 `@` 名只用于读，写入一律走安装路径。
- 测试台 `model:` 的 JSON Schema 描述补一句；`extends:` 同。旧版本 SimuBench 读到 `@` 名报"未知型号"，可以接受。

### 10.4 错误（本机 API → HTTP，CLI → 退出码 2，MCP → 工具错误）

| 情况 | HTTP | 诊断码 |
| --- | --- | --- |
| 测试台引用的 `@x/y` 未安装 | 校验 error | `E-SHARED-NOT-INSTALLED`（提示安装命令） |
| 已装文件被改动（sha256 不符） | 校验 error | `E-SHARED-MODIFIED`（提示复制为我的仪表或重新安装） |
| 编辑共享定义 | 403 | 沿用现有"不在工作区"措辞，改成"共享仪表只读" |
| 卸载时仍被测试台引用 | 409 | 列出测试台路径 |
| 未配置 hub / 未登录 | 409 / 401 | 界面隐藏入口；CLI 提示 `simubench shared login` |
| hub 不可达 | 502 | 本地功能全部照常 |
| 发布校验失败 | 422 | 原样返回 `Diagnostic[]`，与本地校验同码 |
| 发布用到插件组件 | 422 | `E-SHARED-PLUGIN` |

### 10.5 hub 存储

```
publishers(name PK, kind user|team, created_at)
members(team, user, role member|admin)
definitions(owner, id, visibility, description, class, manufacturer, model, created_at, PK(owner,id))
versions(owner, id, version, sha256, bundle_sha256, note, published_by, published_at, yanked_at,
         simubench, PK(owner,id,version))
audit(at, actor, action, target, detail)
blobs/<bundle_sha256>.sbdef   # 内容寻址，写一次
```

## 11. 工作包（每包一个 issue；W1–W5 不依赖 hub）

每包都要过 `scripts/check.sh`，测试按 D49 分层（单元 ≤100ms，hub 的启动级测试复用一个进程内 app）。

| # | 工作包 | 依赖 | 主要改动 | 验收（测试名建议） |
| --- | --- | --- | --- | --- |
| W1 | `@owner/id` 解析与只读 | — | `library/loader.py`（`Library.path`、`_extends_target`）、`definitionfiles.py`、`models/bench.py` 描述 | `@a/b` 在 `instruments/@a/b.yaml` 找到；裸 id 查找顺序不变；`extends: "@a/b"` 生效；编辑返回 403（`test_shared_resolve.py`） |
| W2 | `shared.lock` 与完整性校验 | W1 | `models/shared.py`、`library/shared.py`、`engine/validate.py` 接入两条诊断码 | 改一字节 → `E-SHARED-MODIFIED`；缺文件 → `E-SHARED-NOT-INSTALLED`；lock 输出逐字节稳定 |
| W3 | 定义包导出/导入（无 hub） | W2 | `library/bundle.py`；CLI `shared export <id> -o x.sbdef`、`shared import x.sbdef --as @a/b` | 本地祖先被展平、内置父保留并记哈希；恶意包（`..`、链接、多余文件、超限）被拒；导入记版本历史（`Source` 加 `"shared"`） |
| W4 | 目录来源字段与仪表库三分组 | W1 | `DefinitionSummary.source`/`shared`（保留 `workspace` 一个版本）；`web/` 首页与元件库分组（原型 P1、P6 左栏）、验收页头部（P4）、导入定义包；i18n | API 类型重新生成；分组顺序 我的→共享→内置；未配置 hub 时无共享搜索入口（vitest + 一条 e2e） |
| W5 | 卸载与引用检查 | W2 | `/api/shared/{owner}/{id}` DELETE；CLI `shared remove` | 被引用时 409 列出测试台；卸载按 lock 的 files 删除且只删这些 |
| W6 | `simubench hub` 服务骨架 | W3 | `hub/app.py`、`hub/store.py`（SQLite）、第 7.1 节路由、第 10.5 节表；CLI `hub --db --blobs --port` | 发布→列表→下载→撤回闭环；同版本不可覆盖；服务端重跑校验；OpenAPI 进 `schemas/` |
| W7 | 本机客户端与登录 | W6 | `shared login/search/install/upgrade/publish`；`/api/shared/*` 转发；令牌存用户配置目录；界面：共享库抽屉与安装（P2）、发布（P3）、设置（P7）、工作台缺失仪表（P6） | 令牌不出现在诊断包、日志、运行记录（`test_security.py` 加一条）；hub 不可达时本地全部功能通过 |
| W8 | 升级与变更 | W7 | upgrade 返回 `definition_changes`；新版本横幅与「版本」标签（P4）、升级对话框（P5） | 升级记版本历史；撤回版本仍可用并提示 |
| W9 | 可见性、团队、SSO、审计 | W6 | hub 认证中间件（OIDC 或反代头二选一，见第 12 节问题 1）、`members` | 未授权用户搜不到 team 级、下载 404（不是 403，不泄露存在） |
| W10 | MCP 工具 | W7 | `search_shared`、`install_shared`（`confirm=true`） | 无发布工具；`tests/test_mcp.py` 覆盖 |
| W11 | 文档同步 | 各包 | `security.md`（hub 令牌、包解析）、`architecture.md`、`glossary.md`（共享仪表、定义包、锁文件）、`codebase-map` 技能 | `check_skills.py` 通过 |

建议顺序：W1→W2→W3（此时两人已能靠拷贝 `.sbdef` 共享）→W4、W5 并行→W6→W7→W8/W10→W9。
W4、W7、W8 的界面照原型 P1–P7 做；原型没画到的交互与措辞属于 Web 交互（`核心`），回到设计。

## 12. 待确认（对齐时回答；问题 1 决定 W9 的做法）

1. hub 由谁部署运维，接哪套公司认证（LDAP / OIDC / 反代头）？
2. 默认可见性 `team` 是否合适？团队从哪里来（同步公司组织架构，还是在 hub 里手建）？
3. 共享单位只有仪表，还是也要共享测试台（D65 说测试台是用户资产）？本稿只做仪表。
4. 是否需要发布审核（例如 `org` 级可见要管理员批准）？
5. 内置仪表的父定义随版本变化时，只提示还是在安装端锁住父定义（把父定义也打进包）？
