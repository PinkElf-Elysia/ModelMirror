# V19 Coding Substrate：架构冻结与 Strangler 边界

## 1. 单一目标

- 本次要完成：把 Coding Worker 冻结为平台级 Coding Substrate，通过中立端口切断 API、模块 SDK 与 v13 写回桥对 Store、Workspace、Harness 和具体 Provider/Executor 的穿透。
- 本次明确不做：新增 Provider、工具、语言、Agent、UI、数据库迁移、真实模型校准、48/288 认证或任何 OpenCode 等效表述。

## 2. 基线与证据

| 结论 | 等级 | 证据 |
| --- | --- | --- |
| 开工主线为 `365bc750`，包含 V18 最终合并 | 已证实事实 | `git fetch origin --prune`、`git rev-parse origin/main` |
| 当前主检出包含大量无关改动，不能用于 V19 | 已证实事实 | 主检出 `git status --short --branch` |
| Worker API 与 SDK 直接读取 `service.store` | 已证实事实 | `server/coding_worker/api.py`、`server/coding_worker/sdk.py` |
| v13 handoff 直接读取 Worker Store、Evidence 与 Workspace | 已证实事实 | `server/coding_runtime/api.py::handoff_coding_worker_task` |
| ACP 与 Codex App Server 适合作为私有 Harness Driver 生命周期来源，不替代平台沙箱和控制面 | 建议方案 | ACP v2 schema、Codex App Server 官方协议 |

## 3. 所有权与依赖方向

| 领域 | 唯一所有者 | V19 处理 |
| --- | --- | --- |
| 准入、幂等、调度、预算、审批、交互状态 | ModelMirror `TaskControlPlane` | 保留现有语义，改由端口访问 |
| 任务、事件、Workspace、Evidence、Artifact 读模型 | `InteractionProjection` | SSE 与模块 SDK 只读取持久投影 |
| 模型会话、上下文和生成循环 | `HarnessDriver` | V19 仅包装 Provider v4；ACP/Codex 留到 V20 |
| Shell、服务、进程和 LSP | Tool Broker + `ExecutionBackend` | Broker 继续掌握策略、operation 与 reconcile |
| Harbor/Parity | `EvaluationAdapter` | 只在评测 profile 加载 |
| 宿主项目写回 | v13 | 只接收不可变 `WritebackCandidate` |

依赖方向固定为：模块和 Console → Control/Projection → Store/Scheduler/Policy；Scheduler → Harness Driver；Tool Broker → Execution Backend；Harbor → Evaluation Adapter → 正式 Worker API。供应商帧、端口、物理路径、凭据和原始 session ID 不得进入公共契约。

## 4. 实施轮次

1. PR A：增加端口、职责文档和 AST 依赖门禁；只冻结现有债务，不改变生产路径。
2. PR B：增加 legacy adapter、runtime composition、评测隔离和测试态影子对照。
3. PR C：切换 Worker API、模块 SDK 与 v13 handoff，清空 PR A 冻结的跨边界例外。

每个逻辑代码提交最多五个文件。不拆分大型 `service.py`、`store.py` 或 `api.py`，不增加生产依赖。

## 5. 公共接口与数据

- `/api/coding-worker/v1`、`/api/coding`、`/api/agent-workspace` 路径、JSON/SSE、错误码和 feature flags 不变。
- `TaskSpec`、runtime protocol、Provider v4 checkpoint、数据库表和已有任务不迁移。
- 内部 `CodingWorkerModuleClient` 最终接收 `CodingSubstrateHandle`，不再公开具体 Service。
- 不新增 V19 开关；PR C 可通过恢复上一镜像或回退提交撤销，无数据回滚。

## 6. 验收矩阵

| 检查 | 命令或场景 | 预期 | 状态 |
| --- | --- | --- | --- |
| PR A 端口/门禁 | `python -m pytest server/tests/test_coding_worker_architecture.py -q` | 端口齐全，既有债务精确冻结 | 未运行 |
| Worker 专项 | `python -m pytest server/tests -q -k coding_worker` | 通过 | 未运行 |
| 后端全量 | `python -m pytest server/tests/ -q` | 无 V19 新增失败 | 未运行 |
| 前端 | `npm.cmd --prefix client run test:run`、`npm.cmd --prefix client run build` | 公共契约无回归 | 未运行 |
| Compose | `docker compose config -q` | 配置有效 | 未运行 |
| V18 | `python scripts/coding_worker_harness.py compile`、`smoke` | bundle 不变，Fake smoke 通过 | 未运行 |
| 安全 | `git diff --check`、敏感信息与禁止产物扫描 | 无异常 | 未运行 |

## 7. 风险、停止与回退

- 主要风险：机械切换遗漏某个读写路径、评测 profile 被误装入生产启动、handoff patch 语义漂移。
- 停止条件：需要数据库迁移、公共 API 破坏、真实密钥、Provider checkpoint 迁移，或出现无法归因的既有全量失败。
- 回退：PR A/B 可独立回退；PR C 恢复上一容器镜像或回退其提交。Store、Workspace、Evidence、Recovery 与 v13 数据均不删除。

## 8. 完成定义

- [ ] API、SDK、handoff 对 Worker 具体对象的跨模块依赖为零。
- [ ] 唯一 concrete wiring 位于 composition root 与 adapter。
- [ ] 评测关闭时生产启动不加载 Parity/Harness V3。
- [ ] 现有任务、checkpoint、公开响应和写回行为无迁移、无差异。
- [ ] 后续 ACP/Codex Driver 不需要修改 TaskSpec、Tool Broker、Evidence 或 v13 写回。
- [ ] 交付结论只表述为“Coding Substrate 架构边界完成”。
