## 助手设置面板 422 显示服务端原因，不再永久骨架（Issue 1753）

| 完成条件 | 自检 |
| --- | --- |
| 配置查询失败时，设置面板显示服务端 422 正文，而不是永久骨架 | ✅ `AssistantSettings.tsx` 查询 `isError` 渲染 `role="alert"` 错误行（`assistant.config.loadError` 模板 + 服务端 detail）；测试 `SIMUBENCH_LLM_TIMEOUT=abc` 场景由 `model.test.ts` 的 422 mock 覆盖（断言含点名与原文、骨架消失、表单未渲染） |
| 文案中文优先、英文其次（D23），新增键走 `web/src/i18n/{zh,en}.ts` | ✅ `loadError`：zh「读取设置失败：{{message}}」、en「Failed to read the settings: {{message}}」 |
| Web 测试覆盖错误态（现有测试模块内扩展，不新开文件） | ✅ `web/src/features/assistant/model.test.ts` 加 `AssistantSettings` describe 两用例（422 错误态 + 200 表单照常）；`pnpm -s vitest run src/features/assistant/model.test.ts` 10 passed，全量 428 passed |
| 200 时的表单行为不变 | ✅ 200 分支原样（`SettingsForm`）；`pnpm typecheck && pnpm lint && pnpm test` 全绿 |
