## WS 重连传输样板合一：退避与鉴权一个适配器（Issue 1733）

| 完成条件 | 自检 |
| --- | --- |
| 「连接→退避→状态回调」传输适配器单点：`openWsStream(url, handlers)`（`web/src/api/wsTransport.ts`）持有 ws/closed/attempt/timer 生命周期与清理，返回清理函数；退避公式单源 `backoffMs`（1 s 起、倍增、10 s 封顶），鉴权 URL 单源 `wsUrl`（proto/host + `?token=`），4401→unauthorized 分支单源在适配器 | `grep -rn '2 \*\*' web/src --include="*.ts" --include="*.tsx"` 退避公式只剩 `wsTransport.ts:11` 一处（probe 的两处是无关位运算）；`grep -c "encodeURIComponent(getToken" web/src/api/events.ts web/src/api/workspaceEvents.ts` 各为 0 |
| 两个流只剩各自的 onmessage 语义，重连行为无变化：会话流留批量 flush+version 与 since 游标（4404→gone 经 `onClose` 回答），工作区流留 reducer+resync（首开 `readBeforeOpen` 判据不变）；`ConnectionState` 类型随适配器，`events.ts` 转出保持 `TopBar` 导入不变 | `web/src/api/events.test.ts`、`hooks.test.tsx` 的 workspace 用例零改动通过；`cd web && node_modules/.bin/vitest run` 41 文件 425 passed（合并 main 后基线 416 + 本条 9）；`tsc -b --noEmit`、`eslint` 全绿 |
| 适配器退避封顶/4401 行为有 vitest（fake WebSocket + fake 定时器） | `web/src/api/wsTransport.test.ts` 9 例：退避表 1000→2000→4000→8000→10000 封顶、按点重连、成功 open 重置倍增、4401→unauthorized 停止重试、流自有 close code（4404）截停、首开非重连/后续开是、清理取消定时器、清理后落下的 close 不重连、wsUrl 拼接 |
| e2e 原样存活；纯重构不写 D | e2e 零改动（CI 跑）；`grep -n "1733" docs/decisions.md` 无新条目；skill 的 api 层描述无需改（`events.ts`/`workspaceEvents.ts` 路径与职责行不变） |
| `scripts/check.sh` 全绿（看退出码，除已知存量） | 退出码见本条 PR（test_select_tests 预算偶发红=存量 #1716，单跑即绿，本条不修） |

写前 fetch origin/main（548afc26，#1742）：新增提交动的是 sessionValues 的 state 事件合并、
progress-log 各自新文件，与本条零交集；已合并进分支。

### 适配器形状裁决

issue 说「一个 hook 或小模块，形式你定」，落成**小模块 + 普通函数** `openWsStream(url: () => string, handlers): () => void`，不落 hook：两个流的连接都住在各自 `useEffect` 里（deps `[sid, onState]` / `[qc]`，回调每渲染新建），hook 形态要么把回调放进 deps 造成重连循环、要么得为 `lastSeq`/`opened` 这类流内状态再造 ref 桥；普通函数由流的 effect 调一次、把返回值当清理，deps 语义原样保留。`url` 是回调而非字符串，每次重连现拼——会话流要在此时读最新 `lastSeq` 拼 `since`，令牌（`getToken`）也随之每次重取，与原行为一致。

`ConnectionState` 随传输搬到 `wsTransport.ts`（它是传输的状态词汇），`events.ts` `export type { ConnectionState }` 转出，`TopBar` 的导入路径不动。事件流的 4404→gone 不进适配器：那是会话流自己的语义，走 `onClose(code) → ConnectionState | undefined` 回答，返回即截停（不重连）；工作区流不提供该回调。`onOpen(reconnect)` 只做流的开侧工作（会话流在此原子地合并 status+version bump，D64），失败态经 `onStatus` 报给各自的 store——「connected」的原子合并留在流里，避免拆成两次 setState。
