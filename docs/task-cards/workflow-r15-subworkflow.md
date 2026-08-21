# 工作流 R1.5 PR 2：同步子流程闭环

## 基线与目标

- 基线：`origin/main@fc79bf9a`（PR #224 合并提交）。
- 分支：`codex/workflow-r15-subworkflow`，独立 worktree。
- 目标：增加 `workflow_call_entry` 与 `invoke_workflow`，完成固定版本、同步、可审计的内部子流程调用。
- 不新增公开远程调用工作流 API，不修改 Xpert Automation API，不复制 n8n 实现或 Schema。

## 允许范围

- 工作流 NodeContract、Registry、静态验证和经典运行器。
- 独立工作流 Store/API 的 additive v2 执行关系与安全接口摘要。
- 经典画布节点类型、默认数据、配置面和运行关系摘要。
- 563 行能力矩阵、环境开关示例及本轮测试。

## 禁止范围

- 动态目标/最新版、异步调用、可挂起子流程、任意 URL 或公开调用端点。
- continue-on-error、自动重试、补偿、多 Worker/HA、组织 RBAC。
- Xpert 内嵌工作流使用这两个节点；Planner 继续默认关闭。
- 提交、推送或 PR 创建，除非用户后续另行授权。

## 公共接口与持久化

- 新增安全只读接口：`GET /api/workflows/{id}/versions/{version}/interface`。
- `GET /api/workflows` 的 `trigger_kind` 增加 `call`。
- Store 保持 `workflow-deployments-v2`，仅增加可选执行关系表和字段；旧 v1/v2 文件缺字段时补空值。
- 执行摘要增量返回父、根、调用节点和测试模式，不返回输入正文、变量快照或凭据。

## 风险与护栏

- 风险等级：高。涉及经典 runner、文件持久化、内部递归和取消传播。
- 固定目标必须已发布且该精确版本当前启用，并以 `workflow_call_entry` 为唯一入口。
- 发布/启用时拒绝自调用、固定版本间接环、可等待节点、缺失必填输入和字面量类型错误。
- 运行时限制最大深度 8、每根执行最多 32 个后代；每个父执行和调用节点只物化一个子执行。
- `WORKFLOW_SUBWORKFLOWS_ENABLED=false` 时允许编辑和发布，但相关版本禁止启用和运行。

## 验收

- 最小：NodeContract/Registry/validate、Store/API、同步调用、输入类型、环/深度/数量、失败/超时/取消/结果复用测试。
- 回归：R1 定时/Webhook/等待、R1.5 失败入口、变量治理、Xpert 禁用和 `/workflow-native/validate`。
- 前端：工作流 Vitest、typecheck、生产构建。
- 最终：全量 `server/tests/`、`git diff --check`、敏感信息扫描；时序测试使用 fake clock。

### 本地验证记录

- 子流程专项：`9 passed`；覆盖固定版本、必填/默认值/类型、停用目标、环、深度/数量、父取消、超时、失败传播、运行中复用，以及持久任务已终态但部署摘要未落盘的崩溃窗口。子流程与 NodeContract 最终专项合计 `21 passed`。
- 受影响后端 9 组回归：`191 passed`；最终全量再次覆盖这些路径。
- 工作流前端 Vitest：`89 passed`；前端生产构建通过（仅保留既有 chunk size 警告）。JSON 固定值在未完成语法下只保留本地草稿，不再静默保存为字符串。
- 最终全量后端：`3386 passed, 29 skipped, 1 failed`。唯一失败为无交集的 `test_revert_without_transaction_evidence_rejects_same_content_replacement`：干净 `fc79bf9a` 基线按整文件运行 `49 passed`，功能 worktree 同文件复跑也为 `49 passed`，确认是文件身份/inode 时序型不稳定项；本 PR 不混入 coding host 修复。
- Docker 单实例真实 API：固定版本调用完成、错误字面量类型发布返回 422、子流程失败传播、接口合同和父子执行摘要均通过；Store v2 的关系表未保存调用输入。
- 反证补强：租约过期时先用 `WorkflowExecutionStore` 的 completed/failed/cancelled 终态原子收敛部署执行，并使用原租约 token 防止取消竞态覆盖，禁止崩溃窗口重复执行已完成子流程；timeout 使用 fake clock。调用节点合同标记为非幂等，不对目标工作流的外部副作用作过度承诺。

## 回退

先关闭 `WORKFLOW_SUBWORKFLOWS_ENABLED` 并停用调用入口/调用方版本，再回滚代码。新增关系表和摘要字段可由旧代码忽略，不删除草稿、发布版本或执行历史。
