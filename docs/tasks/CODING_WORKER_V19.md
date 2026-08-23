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
4. 收口证伪修复：恢复 handoff/preview/parity-export 的既有错误优先级，消除按异常类名耦合，补齐原生 steer seam、执行后端 fail-closed、AST 绕过反例与序列化影子断言。
5. v13 写回加固：新 apply 使用含对象代际的 `g2` 文件身份，阻断“删除后以相同内容重建”冒充原文件；历史两段式身份继续可读、可回退，无数据迁移。

每个逻辑代码提交最多五个文件。不拆分大型 `service.py`、`store.py` 或 `api.py`，不增加生产依赖。

## 5. 公共接口与数据

- `/api/coding-worker/v1`、`/api/coding`、`/api/agent-workspace` 路径、JSON/SSE、错误码和 feature flags 不变。
- `TaskSpec`、runtime protocol、Provider v4 checkpoint、数据库表和已有任务不迁移。
- 内部 `CodingWorkerModuleClient` 最终接收 `CodingSubstrateHandle`，不再公开具体 Service。
- 不新增 V19 开关；PR C 可通过恢复上一镜像或回退提交撤销，无数据回滚。

## 6. 验收矩阵

| 检查 | 命令或场景 | 预期 | 状态 |
| --- | --- | --- | --- |
| PR A 端口/门禁 | `python -m pytest server/tests/test_coding_worker_architecture.py -q` | 端口齐全，跨边界例外最终清零 | 通过；绝对、相对、动态导入绕过均有反例，评测模块不可导入时生产 API 仍可导入 |
| 收口专项 | V19 architecture/runtime/SDK/substrate/API/host API | 原错误语义、影子序列化、生命周期与 fail-closed 通过 | `72 passed` |
| Worker 专项 | `python -m pytest server/tests -q -k coding_worker` | 通过 | `413 passed, 5 skipped` |
| Project Host 专项 | apply/commit/undo/snapshot/Windows 模拟五个测试文件 | 新旧文件身份凭据兼容，替换冲突可检出 | `248 passed, 9 skipped` |
| 后端全量 | `python -m pytest server/tests/ -q` | 无 V19 新增失败 | 最终候选：`3995 passed, 29 skipped` |
| 前端 | `npm.cmd --prefix client run test:run`、`npm.cmd --prefix client run build` | 公共契约无回归 | `99 files / 533 tests`，production build 通过 |
| Compose | `docker compose config -q` | 配置有效 | 通过 |
| V18 | `python scripts/coding_worker_harness.py compile`、`smoke` | 任务包不变，Fake smoke 通过 | 通过；4 类 8 条记录，摘要 `472b88ae…60a1df3` |
| 安全 | `git diff --check`、敏感信息与禁止产物扫描 | 无异常 | 通过 |

## 7. 风险、停止与回退

- 主要风险：机械切换遗漏某个读写路径、评测 profile 被误装入生产启动、handoff patch 语义漂移。
- 停止条件：需要数据库迁移、公共 API 破坏、真实密钥、Provider checkpoint 迁移，或出现无法归因的既有全量失败。
- 回退：PR A/B 可独立回退；PR C 恢复上一容器镜像或回退其提交。Store、Workspace、Evidence、Recovery 与 v13 数据均不删除。

## 8. 完成定义

- [x] API、SDK、handoff 对 Worker 具体对象的跨模块依赖为零。
- [x] 唯一 concrete wiring 位于 composition root 与 adapter。
- [x] 评测关闭时生产启动不加载 Parity/Harness V3。
- [x] 现有任务、checkpoint、公开响应和写回行为无迁移、无差异。
- [x] 后续 ACP/Codex Driver 不需要修改 TaskSpec、Tool Broker、Evidence 或 v13 写回。
- [x] 交付结论只表述为“Coding Substrate 架构边界完成”。

## 9. 实施结果与后续接入

- `runtime.py` 是唯一生产组装根；Legacy Provider v4、Executor、Store/Workspace 与评测能力分别由 adapter 注入 `CodingSubstrateHandle`。
- Worker API 的命令只进入 `TaskControlPlane`，读取与 SSE 只进入 `InteractionProjection`；模块 SDK 实例只保留这两个端口，不保留 Service 或完整 substrate。
- v13 handoff 只接收已完成、host snapshot、Acceptance 有效且 tree/hash 绑定的 `WritebackCandidate`。候选生成使用 rename-disabled Diff；v13 继续独占规范化、exact-head、apply/commit/undo/recovery。
- V20 新增 ACP 或 Codex Harness 时只允许增加 Driver adapter 和部署 wiring；不得修改 `TaskSpec`、Tool Broker、Evidence、公共 API 或写回协议。
- 收口审查先实际复现了 handoff 503 被误映射为 409、preview/parity 检查顺序漂移、AST 门禁可绕过和缺失 Executor 方法泄漏 `AttributeError`；以上均以失败反例修复，不把原有绿测当作正确性证明。
- 最终收口已同步 `origin/main=7e543d8c`（含 #255）。以同步前共同点 `c0c7c89b` 复核，主线 18 个变更文件与 V19 32 个变更文件仅交叉 `docs/ARCHITECTURE.md`、`docs/DEPLOYMENT.md`，均自动合并；产品代码无交叉，Provider Chat 内容仅随主线进入。
- 全量证伪曾揭示 v13 只用 device/inode 识别文件，inode 复用时无法拒绝相同内容替换；最终候选改用向后兼容的 `g2` 代际身份，原失败用例和 Project Host 全专项均通过。
- 本轮没有调用真实模型、运行 48/288 校准或认证，也不形成任何能力接近/等效结论。
