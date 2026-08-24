# 任务卡：AR0 模镜科研独立扩展

## 1. 单一目标

- 本次要完成：在 `extensions/ai-research/` 内建立 fixture-only 的 Inspect 执行、持久控制账本与 MLflow 证据闭环。
- 本次明确不做：模型调用、真实 EvalPack、科研评分、产品界面、主服务接入、多租户、Commit、Push、PR 或部署。

## 2. 证据

| 结论 | 等级 | 证据路径或命令 |
| --- | --- | --- |
| 当前实施基线是 `origin/main@e7f561c5abf65269e6be05c6b6e75f40b8f14604` | 已证实事实 | `git rev-parse HEAD` |
| 当前主检出区存在大量用户修改，必须使用独立 worktree | 已证实事实 | 主检出区 `git status --short` |
| Inspect 0.3.260 的单次 eval 可能在任务 error 时退出 0 | 已证实事实 | G0 固定版本运行证据与公开 EvalLog/CLI 契约 |
| Inspect 0.3.260 在 Linux 无网容器支持 detach、ctl list 与 cancel | 已证实事实 | G0 Linux 容器运行证据 |
| MLflow 3.15.1 的 SQLite、artifact 和 trace 可跨两次重启恢复 | 已证实事实 | G0 固定版本运行证据 |
| AR0 不构成真实科研或模型评测能力 | 已证实事实 | 已批准计划的 fixture-only 边界 |

## 3. 影响范围

- 允许修改路径：`extensions/ai-research/**`、`.dockerignore`、`.github/workflows/ai-research.yml`。
- 禁止修改路径：`client/**`、`server/**`、根 `docker-compose.yml`、现有 Plugin、Studio 路由和用户持久化数据。
- 预计文件数：约 30 个，按每批不超过 5 个文件实施和验证。
- 影响路由/API：仅模块自有、loopback 暴露的 `/healthz`、`/readyz` 和 `/api/v1/**`。
- 影响持久化数据：新增模块独立 SQLite、MLflow backend/artifact 和 Inspect log 命名卷；不迁移主库。
- 新增或升级依赖：仅模块镜像内固定 Inspect 0.3.260、MLflow 3.15.1、FastAPI/Uvicorn 与完整传递锁。
- 涉及密钥/网络/文件/子进程/公开访问：无密钥；Tracking 私网；Worker 无网；受限文件与 Inspect 子进程；端口仅绑定 `127.0.0.1`。

超过 5 个文件时说明无法安全拆分的原因：两个隔离运行时、Compose、公共契约、持久化、验收脚本和测试不能安全合并为少量大文件；通过小批次保持单一职责和独立回退。

## 4. 验收标准

### 正常场景

- Given：模块显式启动且 Tracking、Worker ready。
- When：分别运行 success、task_error 和 long_running_cancel 夹具。
- Then：控制账本、原始 Inspect 状态、归一化 outcome、MLflow run/trace/artifact 和哈希 receipt 一致且可重启恢复。

### 失败场景

- Given：畸形 EvalLog、退出 0 的 task error、重复幂等键、路径穿越、MLflow 中断或取消竞态。
- When：执行针对性攻击。
- Then：fail-closed、保留原始事实、不生成科研分数、不丢失本地证据。

## 5. 实施顺序

1. 边界、来源锁和依赖锁。
2. Worker 公共 CLI 适配与 UDS 协议。
3. Control API、SQLite 与状态归一化。
4. MLflow outbox、receipt 与 Compose 隔离。
5. 单元、容器验收、零增量和反证审计。

## 6. 验证矩阵

| 检查 | 命令或步骤 | 预期 | 状态 |
| --- | --- | --- | --- |
| 语法/类型 | 模块 pytest | 无错误 | 通过 |
| 目标测试 | Control/Worker/receipt 契约测试 | 正常和攻击场景通过 | 通过 |
| 回归测试 | 主前端构建哈希、默认 Compose 清单 | 与实施前一致 | 通过 |
| 构建 | 两张模块 OCI 镜像 | 固定版本且可启动 | 通过 |
| Docker/人工验收 | `scripts/verify.ps1 -Base origin/main -Mode Full` | 三类夹具和两轮重启通过 | 通过 |
| 敏感信息扫描 | 边界验证器 | 无凭据或主仓运行时耦合 | 通过 |

状态只允许：`通过 / 失败 / 未运行 / 不适用`。

## 7. 风险与停止条件

- 主要风险：把 Inspect 进程退出码误当终态；取消覆盖原始 error；MLflow 不可用造成证据丢失。
- 兼容风险：Inspect/MLflow 公开 CLI 或 schema 与固定版本证据不一致。
- 安全风险：Worker 获得网络、任意参数或父仓路径；未认证服务暴露到非 loopback。
- 触发停止的条件：出现未知许可证、必须接触真实密钥、需要修改禁止路径、无法保留原始终态、默认包发生未解释变化。
- 需要用户确认的问题：无；产品和未来 EvalPack 选择不属于 AR0。

## 8. 回退

1. 停止模块 Compose，移除本模块、专属 CI 和 `.dockerignore` 单行规则。
2. 无活动版本或平台指针需要恢复。
3. 默认保留命名卷；删除卷必须另行授权。
4. 重新执行主前端哈希和默认 Compose 清单比较。

## 9. 完成定义

- [ ] 实现只覆盖声明范围。
- [ ] 正常与失败路径均有验证。
- [ ] 公共接口和数据影响已说明。
- [ ] Diff 已审查，无用户改动被覆盖。
- [ ] 无密钥、运行存储或构建产物进入提交。
- [ ] 文档与 Harness 已同步。
- [ ] 未知产品信息仍明确标为待确认。
