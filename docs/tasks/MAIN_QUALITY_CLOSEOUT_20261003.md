# 主线质量门禁独立修复

状态：用户已授权提交 Draft PR；完整门禁仍未完成，不可据此标记可合并。

## 范围与基线

- 执行基线：`origin/main@8c2a0120226be26f05c875f81c122589b216525d`。
- 独立工作树：`C:\tmp\modelmirror-main-quality-closeout-20261003`。
- 分支：`codex/main-quality-closeout-20261003`。
- 只处理已在干净主线复现的质量阻塞，不包含 CW10 生产实现、固定候选或运行数据。
- 不调用 Provider、不刷新在线目录、不操作共享栈。后续用户明确授权提交 PR；以 Draft 提交并披露未完成门禁，不自动合并。

## 可验证批次

1. 测试契约：修正过期音频目录版本断言；分时价格使用合成夹具，保留目录快照对当前空时段的独立断言。检查时段边界、默认价格和卡片展示，不修改真实价格。
2. 帮助资产：核对真实文件格式与引用，保留历史证据；修复文章必需结构，不伪造新实操或付费验证。
3. CI 准备：根据实际子项目依赖和 Node 引擎范围补齐环境，不跳过门禁或降低断言。实际 GitHub CI 未运行前不称 CI 通过。
4. 集成：重点测试、前端全量/类型/构建、帮助图片、后端相关测试、语法及敏感信息检查。后端全量与环境失败单列，不能沿用 CW10 中断结果。

## 已确认根因

- `AudioCatalogService` 发布版本为 `modelmirror-audio-contracts-2026-10-02-deepgram-flux`，两项测试仍要求 9 月 25 日版本；隐私断言和可用性断言保留。
- `models.refresh.test.ts` 明确要求 Flash Vision 条目没有分时时段；两个通用价格测试却仍要求该同一条目有五个时段。用独立固定夹具消除测试间矛盾，不向生产目录补回已移除价格。
- RPG 帮助文章缺少必需章节；保留原有行为和验证边界，补齐适用对象、开始前、真实范例、常见问题和限制。
- CI 仅安装 client 和 worker 依赖，遗漏测试读取的 RPG 子项目；其 Node 引擎要求为 24.18.0，原 CI 配置为 22。补齐锁定依赖安装并对齐 Node，不移除现有测试或安全策略。

## 当前验证回执

- 音频基础与 TTS：71 passed，隔离本地测试，无 Provider 调用。
- 价格、卡片与目录回归：96 passed；帮助中心结构：15 passed。
- `git diff --check`：通过。
- `npm.cmd run test:run`：149 个文件、1109 项 Vitest 测试通过，响应头 Node 测试 1 项通过，退出码 0。
- `npm.cmd run build`：TypeScript 与生产构建通过，保留现有大包警告。
- 结构化输出后端回归：23 passed，包含 RPG Schema 校验；锁定依赖安装未修改 lockfile。
- 帮助图片：五张未引用历史资产原样归档并同步证据链接，哈希一致；两张公开图片按后续压缩授权使用 900px 无抖动 256 色 PNG，逐张查看后采用，原图保留。`npm.cmd run verify:help-images`：23 篇文章全部通过。
- 提交前刷新：`origin/main` 仍为 `8c2a0120`，无上游新增提交。
- 最终图片入包后的 `npm.cmd run build`：通过，保留大包警告。
- 后端全量首次启动缺少本工作树 Agency worker 构建产物，已中断；按 CI 原步骤补齐锁定依赖和构建，未修改源码。
- 补齐环境后的全量范围 `server/tests/ -x`：63 passed、1 failed，未完成全量。失败为 `test_agent_upstream_port.py::test_started_worker_crash_after_model_request_is_never_restarted`；独立重跑仍 1 failed。
- 失败中预期 `EngineUnavailableError` 被进程清理发送时的 `ConnectionResetError` 覆盖。`server/agent_upstream/port.py` 与对应测试相对 `origin/main` 无差异；不将其归因为本次改动，也不擅自扩大到 Runtime 修复。
- 独立静态预览 `127.0.0.1:15519` 已实际打开记忆宫殿帮助页并核对章节；未执行 RPG 模型调用或完整教程功能重放，不能计为完整帮助验收。
- 当前 PR 阻断：后端失败及未完成的全量、完整帮助验收。实际 GitHub CI 未运行。未提交、推送或创建 PR。

## 验收与回退

### 经授权追加：worker 断管异常

用户在提交门禁发现失败后明确授权修复。限定 `server/agent_upstream/port.py` 的发送边界与对应测试：将 write/drain 的 ConnectionError 转为既有 EngineUnavailableError，保留 cause；取消信号仍透传。不改变已启动 worker 禁止重试的规则。覆盖两个阶段的 BrokenPipeError/ConnectionResetError、取消以及真实子进程崩溃后不重复模型请求、清理活动注册。验证先运行完整 port 测试，再运行 agent_upstream 相关回归。回退仅撤回异常转换及新增测试，不涉及数据迁移或 Provider 调用。

修复后验证：port 测试 9 passed / 1 skipped；全部五个 agent_upstream 测试文件 27 passed / 2 skipped。原失败用例通过，新增写入/排空错误及取消测试通过。`git diff --check` 通过。此结果不替代修复后的全量后端或部署环境验收，提交门禁继续待完成。

测试断言不得删除或宽松化，不新增生产依赖。每个修改微批最多五个文件；依赖仅用原 lockfile 安装。回退只恢复本分支相应测试、文档、资产或 CI 配置，不迁移数据、不动 CW10 或原预览。

所有验证状态以实际回执为准；本任务不能代替 CW10 的完整回归、教程或真实模型验收。
