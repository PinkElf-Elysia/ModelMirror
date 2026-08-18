# 任务卡：WORKFLOW-R0-R1 独立发布与触发恢复闭环

## 1. 单一目标

- 本次要完成：以 `911593f` 为基线收口节点事实，并为 classic `/workflow` 增加独立草稿/不可变版本/部署运行面，以及 `scheduled_start`、`http_event_entry`、`http_event_reply`、`suspend_wait` 四个自研节点。
- 真实运行验收修复：将既有 `llm` 提升为完整 NodeContract，使发布触发工作流可调用模型；允许无 HTTP 回执的 timer continuation 返回 202 后持久恢复。
- 本次明确不做：异常触发、服务专用触发、消息队列、子工作流、自动重试、多 Worker/HA、组织级 RBAC、匿名 Webhook、n8n 代码或资源复用。

## 2. 证据

| 结论 | 等级 | 证据路径或命令 |
| --- | --- | --- |
| 实施基线为 PR #213 合并提交 | 已证实事实 | `git rev-parse HEAD` -> `911593f505b05b01037769f578e21f22d2a1c9af` |
| NodeContract V3 已覆盖全部 NativeNodeKind | 已证实事实 | `server/workflow_native/node_contracts.py`、`server/tests/test_workflow_node_contracts.py` |
| classic 草稿当前仅浏览器持久化 | 已证实事实 | `client/src/utils/workflowStorage.ts` |
| durable continuation 已支持文件快照、lease 与 wait target | 已证实事实 | `server/xpert_runtime/execution_store.py` |
| Xpert Automation 已有 once/interval/cron，但目标绑定 Xpert | 已证实事实 | `server/xpert_runtime/automation_store.py` |

## 3. 影响范围

- 允许修改路径：`server/workflow_*`、`server/xpert_runtime/workflow_node_registry.py`、classic workflow runner 接线、`client/src/components/workflow/`、workflow 类型/API、相关测试与文档。
- 禁止修改路径：聊天/RAG/MCP/模型市场、Xpert Automation 公共契约、真实 Runtime Store、`.env`、依赖锁文件、n8n 源码或资产。
- 预计文件数：约 18–24 个，分 R0、R1 后端、R1 前端、文档四个可独立验证批次。
- 影响路由/API：新增 `/api/workflows*` 和 `/api/workflow-hooks/{hook_id}`；现有 `/api/workflow/run*` 保持兼容。
- 影响持久化数据：仅新增 `AGENT_TASK_STORAGE_DIR` 下 workflow project/deployment/execution 快照；不迁移或删除旧数据。
- 新增或升级依赖：无。
- 涉及密钥/网络/文件/子进程/公开访问：涉及一次性 Webhook key、文件持久化和可选外部 POST；默认关闭 Webhook，无子进程。

超过 5 个文件时说明无法安全拆分的原因：四个节点必须同步前端类型、后端 Schema、NodeContract、Validator、Registry、runner、持久恢复和测试；拆掉其中任一层会形成可拖拽但不可运行或可运行但无法校验的危险中间态。仍按 R0、发布 Store、运行时、前端四批逐批验证。

## 4. 验收标准

### 场景 1

- Given：一个只含一个 `scheduled_start` 入口的已发布版本。
- When：到期、进程重启或重复轮询。
- Then：同一 occurrence 最多创建一次持久执行，等待节点可从 continuation 恢复。

### 场景 2

- Given：一个启用的 `http_event_entry -> ... -> http_event_reply` 版本。
- When：使用正确一次性 key 与 `Idempotency-Key` 重复 POST。
- Then：仅执行一次，30 秒内返回显式回执，否则返回 202 和同一 execution ID。
- Given：一个不含回执的 `http_event_entry -> suspend_wait -> output` 版本。
- When：私有事件进入 timer wait。
- Then：立即返回 202，持久化状态不含原始正文；到期后执行与部署摘要同步完成。

### 失败场景

- Given：Registry 不可用、错误密钥、超限正文、revision 冲突、多个入口或 reply 上游含 wait。
- When：新增节点、调用 Hook 或发布。
- Then：分别 fail-closed；旧工作流仍可加载和运行。

### 配置体验

- 定时启动使用日期时间控件、秒/分钟/小时/天单位和常用日历规则；原始五段 Cron 只作为高级选项。
- HTTP 入口可限制 JSON/纯文本和 64 KiB/256 KiB/1 MiB 正文，并分别登记完整事件与请求正文全局变量。
- 挂起等待使用时长单位或日期时间 + IANA 时区，恢复事件自动进入全局变量中心。
- HTTP 回执提供常用状态语义、自定义 200–599、文本/JSON 模板和全局变量插入。

## 5. 实施顺序

1. 模型/契约：重定基线审计、四节点类型与完整 NodeContract、工作流项目/版本模型。
2. 校验/安全：入口、reply、wait、敏感值、Webhook key/幂等/限流/正文限制。
3. 执行：发布 Store、trigger coordinator、timer continuation、单实例恢复。
4. 前端：Registry 门禁、服务端保存/发布/激活、四节点配置与一次性密钥展示。
5. 文档：能力矩阵、运行边界、回退和验证证据。

## 6. 验证矩阵

| 检查 | 命令或步骤 | 预期 | 状态 |
| --- | --- | --- | --- |
| 语法/类型 | `python -m py_compile ...`、`npm.cmd run typecheck` | 通过 | 已通过 |
| 目标测试 | Workflow contracts/registry/deployments/triggers tests | 通过 | 本轮合同/发布/HTTP timer/审计 135 passed；Registry/Planner 9 passed；工作流前端 84 passed |
| 回归测试 | `python -m pytest server/tests/ -q` | 通过 | 3215 passed、29 skipped；24 个非工作流基线/构建环境失败，见下方说明 |
| 构建 | `npm.cmd run build` | 通过 | 已通过；仅既有 chunk size warning |
| Docker/人工验收 | 单实例发布、真实模型、定时重启、私有 Hook 幂等 | 通过 | 已通过：Luna 定时发布执行返回 `R1_LLM_OK`；HTTP timer 立即 202、到期 completed；重复 Hook 仅 1 条执行；等待跨容器重启恢复完成且 occurrence 仍为 1 |
| 敏感信息扫描 | Diff 与烟测持久卷扫描 key/token/Runtime Store | 无新增秘密 | 已通过；原始正文和 Idempotency-Key 均未持久化 |

全量回归的 24 个失败均不在本任务改动范围：17 个 Agency Worker/执行测试缺少 worker 构建输出，2 个音频目录测试仍期待旧 `2026-08-13-c1` 而基线代码返回 `2026-08-14-c2`，3 个 Skill 测试的容器内 TypeScript 加载/索引生成子进程失败；另有 2 个 RAG Vision 失败策略用例稳定复现为页面失败计数为 0/strict 未失败，与本轮工作流文件无交集。本轮目标测试、Registry/Planner 与前端工作流套件独立全绿。

## 7. 风险与停止条件

- 主要风险：classic runner 目前集中在 `server/main.py`；必须复用而不能复制第二套执行器。
- 兼容风险：新增入口语义不得改变旧 `input` 工作流、SSE 事件或 Xpert 发布行为。
- 安全风险：Webhook key、认证头和正文不得进入日志/checkpoint；默认功能关闭。
- 触发停止的条件：需要明文保存 key、必须迁移/删除已有 Runtime 数据、关键 workflow/Xpert 回归出现无法归因失败。
- 需要用户确认的问题：无；默认值已由批准计划固定。

## 8. 回退

1. 回滚本任务变更；设置 `WORKFLOW_WEBHOOKS_ENABLED=false` 与 `WORKFLOW_TRIGGER_COORDINATOR_ENABLED=false`。
2. 停用新 workflow deployment，不改动旧 Xpert/Automation 活动版本。
3. 新增快照可保留，不需要删除；旧草稿和 `/api/workflow/run` 不依赖新 Store。
4. 重跑 workflow contract/run、Xpert publish、前端构建和 Docker 健康检查。

## 9. 完成定义

- [x] 实现只覆盖声明范围。
- [x] 正常与失败路径均有验证。
- [x] 公共接口和数据影响已说明。
- [x] Diff 已审查，无用户改动被覆盖。
- [x] 无密钥、运行存储或构建产物进入提交。
- [x] 文档与 Harness 已同步。
- [x] 未知产品信息仍明确标为待确认。
