# 任务卡：R6G Expert Team Planner 与 DAG Managed Provider

## 1. 单一目标

- 本次要完成：将 Expert Team Planner 与 DAG 的模型调用接入现有 v16 Managed Provider Route Plan、精确 Binding 和脱敏 Receipt，同时保留 Worker 编排、HITL、返工与恢复语义。
- 本次明确不做：R6H Fusion、R6I Route Agent/Team Chat、Agent Workbench、RAG、多模态、Coding、多租户和计费。

## 2. 证据

| 结论 | 等级 | 证据路径或命令 |
| --- | --- | --- |
| R6F 已合并；R6G 已在提交前变基至最新 `origin/main@af50340c` | 已证实事实 | `git merge-base HEAD origin/main`; `git rev-parse origin/main` |
| Planner 与 DAG 当前都经 Python Host callback 调用 legacy gateway，Worker 不接收 Provider Key | 已证实事实 | `server/main.py::collect_agency_worker_model`; `server/orchestration_worker/client.py`; `execution_client.py` |
| DAG 同时产生普通文本调用和 JSON 验收调用 | 已证实事实 | `server/orchestration_worker/src/execution_connector.ts`; `server/orchestration_worker/test/execution.test.ts` |
| v16 已预留 `expert_team_planner` 与 `expert_team_dag`，但尚未标记数据面已接入 | 已证实事实 | `server/model_router/workload_control.py` |
| 新 Worktree 缺少编译后的 Worker 产物；基线 73 passed，3 个 Worker 启动型失败；显式镜像 Worker 后其中 1 passed、2 仍使用默认缺失路径 | 已证实事实 | R6G 基线 pytest 输出 |

## 3. 影响范围

- 允许修改路径：`server/model_router/`、`server/main.py`、`server/expert_team_agency*.py`、`server/tests/test_expert_team_*`、Expert Team 前端组件与对应测试、控制面/部署文档。
- 禁止修改路径：普通 Chat、RAG、多模态、Fusion、Route Agent、Team Chat、Coding、Provider/newAPI 持久数据与凭据。
- 预计文件数：约 12 个，拆为多个不超过 5 个文件的独立实现批次。
- 影响路由/API：现有 Expert Team 响应加法返回脱敏 `provider_route_receipts`；不删除或重命名字段。
- 影响持久化数据：仅复用 v16 `provider_workload_runs/calls`；不新增迁移、不改写旧数据。
- 新增或升级依赖：无。
- 涉及密钥/网络/文件/子进程/公开访问：涉及 Managed Provider 网络调用和 Agency Worker 子进程；Key 仅留在 Python Host 内存。

超过 5 个文件的原因：控制面 Adapter、两个运行入口、持久化事件投影和前端 Receipt 展示属于同一端到端目标，但按控制面、运行时、前端文档三批分别验收。

## 4. 验收标准

### Managed 正常路径

- Given：入口 Feature Flag 与 Policy 激活，精确模型拥有所需执行形态 Binding。
- When：执行 Planner 预览或 DAG 计划调用。
- Then：每个 Worker request ID 最多派发一次 Managed Provider POST，Receipt 与实际调用数、模型和状态一致，响应不包含连接或凭据。

### 失败场景

- Given：策略 degraded、Binding 缺失、资格漂移、取消、超时、断流或重启中断。
- When：Planner/DAG 尝试调用或恢复。
- Then：在派发前失败关闭，或派发后标记 failed/uncertain；不调用第二 IP、连接、模型或 legacy；uncertain 不自动重放。

## 5. 实施顺序

1. 模型/契约：新增 Expert Team Managed Gateway；DAG 明确要求 unary 与 JSON 两种资格。
2. 校验/安全：接入 feature/policy/degraded 门禁和稳定逻辑调用键。
3. 执行：Planner 与 DAG/HITL/返工/续跑使用独立 Managed Run，终态写入脱敏 Receipt。
4. 前端：显示 Planner 与 DAG 总调用数、阶段状态和原因码，不显示连接细节。
5. 文档：更新架构、部署与控制面轮次状态。

## 6. 验证矩阵

| 检查 | 命令或步骤 | 预期 | 状态 |
| --- | --- | --- | --- |
| 语法/类型 | Python AST；Client `tsc -b`/Vite build | 通过 | 已通过 |
| 目标测试 | Provider Workload、Expert Team Planner/DAG、Managed Gateway 与 Worker Bridge | 通过 | 最终 114 passed |
| 回归测试 | Provider workload、R5/R6 与后端全量 | 通过或明确基线失败 | 最新主线复测：受影响矩阵 114 passed；后端全量 4529 passed、29 skipped |
| 前端组件 | Settings、Planner、DAG 与脱敏 Receipt | 通过 | 最新主线复测：前端全量 112 files、647 tests passed |
| 构建 | 前端生产构建、Server 镜像重建 | 通过 | 已通过；仅保留既有大 chunk 警告 |
| Docker/人工验收 | 独立预览配对、资格、Planner 与 DAG Smoke | 经单独额度授权后通过 | 已通过：Planner 3/3 次、DAG 3/10 次真实调用；请求/实际模型均为 `openai/gpt-4o-mini`，Receipt 与日志 POST 数一致 |
| 敏感信息扫描 | Diff/API/SQLite/日志/浏览器状态 | 无 Key、Prompt 或模型正文 | 静态扫描、真实调用日志与 v16 Receipt 字段已复核；仅保存脱敏模型、状态、指标和 usage |

真实验收前的证伪发现并已回归：Agency Planner 实际使用非流式 YAML/文本合同，原实现错误要求 JSON Object；同时修复了长模型列表截断导致已认证模型无法选择，以及 `degraded_required` 只能回退 Legacy、无法重新批准的设置页缺口。两次失败尝试均在 Provider POST 前阻断，未产生额度调用。Planner 最终使用三次计划内调用（含一次格式修复）；模型生成的人工节点偏离用户目标，已在本地静态编辑并重新校验，未增加 Planner 调用。DAG 以两个专家任务完成三次计划内调用，其中两次 unary、一次 JSON 验收。

## 7. 风险与停止条件

- 主要风险：并发 DAG Call 序号冲突、HITL 恢复重放、派发后 legacy 回退、JSON 裁判错误复用 unary 资格。
- 兼容风险：Expert Team 事件和响应只能加法扩展；既有 legacy flag-off 行为必须原样保留。
- 安全风险：Provider Key 不得进入 Worker、事件、API、SQLite、日志或前端。
- 触发停止的条件：重复 POST、Receipt 与实际目标不一致、需要保存 Prompt/输出、需要迁移数据或扩展到 R6H/I。
- 需要用户确认的问题：真实 Planner 与 DAG 付费 Smoke 的调用次数和执行授权；实现与 Mock 测试不需要新增产品决策。

## 8. 回退

1. 停用 `expert_team_planner` / `expert_team_dag` Policy。
2. 关闭 `MODEL_CONTROL_EXPERT_TEAM_PLANNER_ENABLED` / `MODEL_CONTROL_EXPERT_TEAM_DAG_ENABLED` 并重启，恢复 legacy。
3. 保留 v16 资格与脱敏 Receipt，不删除 Router SQLite、Expert Team 运行数据或 Provider 配置。
4. 回退后重跑 Expert Team Planner/DAG legacy 测试与独立预览健康检查。

## 9. 完成定义

- [x] 实现只覆盖声明范围。
- [x] 正常与失败路径均有自动验证。
- [x] 公共接口和数据影响已说明。
- [x] Diff 已审查，无用户改动被覆盖。
- [x] 无密钥、运行存储或构建产物进入提交。
- [x] 文档与 Harness 已同步。
- [x] 未知产品信息仍明确标为待确认。
