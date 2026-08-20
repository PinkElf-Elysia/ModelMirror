# 原生智能调度与上下文优化

最后更新日期：2026-08-20

> **2026-08-13 决策更新：** 项目维护人已解除“禁止继续建设”的功能冻结，并批准
> 建设 [Model Provider Control Plane](./MODEL_PROVIDER_CONTROL_PLANE.md)。解除冻结
> 不等于批准提高灰度或切换 `native` 默认；500 次请求、14 天、无 P0/P1、故障演练
> 与人工验收门禁全部保留。下方 2026-07-28 内容作为历史基线继续保留。

## 当前状态

ModelMirror 已完成从 OmniRoute 侧车向本地原生能力迁移的阶段 0–4：

- 原生模型服务连接、目录归一、健康状态和加密凭据存储。
- 六种用户策略、硬约束过滤、稳定会话灰度、有限重试、熔断与 LKGP。
- Chat、RAG、工具输出和 Xpert Runtime 共享同一上下文优化内核。
- 严格预算调用前预留、最终 usage 结算、脱敏决策和压缩审计。
- 设置页提供普通用户可理解的连接向导、策略、上下文优化和折叠诊断区。

阶段 5 的算法、遥测和门禁已经进入观察期，但尚未取得默认门禁资格，也不
得通过修改数据或双重模型调用伪造完成。当前默认和紧急回退路径仍是侧车；
普通模型聊天的 default/newAPI 路径始终独立。

## 2026-08-17 冻结解除与发布边界

项目维护人已明确解除 2026-07-28 的增量冻结，允许实施 OmniRoute 行为与
延迟对齐。解除冻结不等于允许切换默认：本轮仅开放 shadow 与 native canary
收集真实单次调用证据，侧车继续默认。默认切换必须等待完整 14 天观察期并
通过本文全部自动门禁与绑定当前版本/配置的人工批准，再由独立 PR 完成。

## 架构边界

```text
Chat / RAG / Xpert Runtime
        |
        +-- context_engine（共享、确定性保护与可选旧对话摘要）
        |
        +-- gateway=default ------> newAPI / OpenRouter（稳定路径）
        |
        +-- gateway=auto ---------> model_router policy
                                      | sidecar
                                      | shadow（只计算，不二次调用）
                                      | native_canary（稳定会话哈希）
                                      ` native（达标后）
```

- `server/model_router/` 只依赖 ModelMirror 的 FastAPI/httpx/Pydantic/SQLite
  边界，不引入 OmniRoute 控制台、数据库或私有 workspace 包。
- `server/context_engine/` 是聊天、RAG、工具和 Xpert Runtime 的共享内核。
- `server/main.py` 只保留请求编排和 SSE 兼容；评分、持久化、连接和压缩
  逻辑不得继续堆入该文件。
- 所有连接、策略、候选统计、决策和压缩记录都携带 `tenant_id`。首期固定
  为 `local`，仓储接口不向调用方暴露 SQLite 细节。

## 用户契约

普通用户只看到：

- **模型服务连接**：选择服务、填写地址和密钥、测试、保存、停用或恢复。
- **智能调度**：均衡、速度、质量、成本、稳定、本地优先。
- **上下文优化**：自动推荐、关闭、标准、强力。
- **运行诊断**：脱敏后的成功率、空响应率、模型与连接、迁移进度。

普通界面不显示 sidecar、candidate、breaker、LKGP 等内部术语。后端错误
只给出“密钥无效”“地址不可达”“没有可调用模型”等行动建议，不返回上游
完整错误体。连接密钥只在后端加密保存，列表、日志和诊断接口只返回摘要。

`/chat/auto` 是统一入口。历史
`/chat/auto?gateway=omniroute` 仍会进入同一 auto 契约，但普通页面不再生成
该参数。`gateway=omniroute` 仅保留为诊断兼容入口。

## 路由规则

六种策略固定为：

| 用户策略 | 后端 ID | 结果导向 |
| --- | --- | --- |
| 均衡推荐 | `auto` | 综合质量、速度与费用 |
| 速度优先 | `fast` | 提高延迟与可用性权重 |
| 质量优先 | `quality` | 提高任务匹配与模型档位权重 |
| 成本优先 | `cheap` | 优先可可靠估价的低成本候选 |
| 稳定优先 | `reliable` | 优先本会话近期成功候选（LKGP） |
| 本地优先 | `offline` | 优先本地可用性与充足配额 |

意图、任务难度、任务适配度、模式权重、速度评分、分层轮换与探索选择直接
等价移植自固定 OmniRoute `release/v3.8.49` / commit
`36f8fd10052f` 的 MIT 许可纯算法模块。溯源、摘要、许可证与修改说明位于
`server/model_router/omniroute_parity/`；固定向量位于
`server/model_router/fixtures/omniroute-v3.8.49-routing.json`。

硬过滤后按“连接 + 模型发布者前缀”建立竞争池，最多 24 个候选；探索只在
距最高分不超过 0.10 的竞争带内发生。均衡、速度、成本和本地优先探索率为
5%，质量优先为 10%，稳定优先为 0%。事故模式、连续故障或候选不足时关闭
探索；稳定优先保持会话 LKGP，直到失败、熔断或硬约束改变。

硬过滤顺序覆盖租户、连接启用、凭据、熔断、输入输出模态、工具能力、
上下文长度和严格预算。能力、权限和预算永不 fail-open；非必要类别偏好
可以回到完整合格池。失败最多切换两个候选，且只允许在尚未向用户输出正文
前切换。空流、零 Token 且无正文、只有 `[DONE]` 均按失败处理。

熔断按租户、连接和模型隔离：

- 连续三次失败进入 `open`。
- 冷却后进入 `half_open`，只作为探测候选。
- 成功关闭熔断；失败延长冷却时间，最长 30 分钟。

Shadow 只保存原生决策，不发送第二次模型请求，不增加费用。
`auto` 使用上游固定的多语言意图与结构化任务难度分类，不发送分类模型请求，
也不保存原始消息。普通“搜索/最新”文字不获得 Perplexity 特判；只有真实
Chat 工具契约才能形成工具能力硬约束。`:batch` 目录条目永不进入实时池。

## 上下文优化

自动模式在“预计输入 + 最大输出 + 5% 安全余量”达到模型上下文的 80%
时启动，按以下顺序处理：

1. 工具输出过滤与重复行折叠。
2. 冗余语句和填充内容压缩。
3. RAG 片段去重与跨消息重复内容折叠。
4. 仍超阈值时，调用现有会话摘要能力压缩旧对话。

系统提示、最新用户消息、工具 schema、代码块、JSON/XML、URL、引用标识
和文件来源永不改写。每阶段都执行保真检查；结构损坏、关键标记缺失、
节省不足 10% 或异常时回退原文。原文仍超出候选上限时返回 422，并建议
减少附件、清理历史或选择更长上下文模型。

路由回执 v2 保持兼容，并可选增加 `task_type`、`selection_kind`、
`ttft_ms` 和 `algorithm_version`。详细原因码与阶段数据默认折叠，不返回
评分矩阵、密钥或完整 Prompt。

## 存储与预算

SQLite 当前 schema 版本为 11；迁移只增量加列/表，旧数据不删除。核心表为：

- `router_connections`
- `router_policies`
- `router_candidate_stats`
- `router_decisions`
- `compression_runs`
- `router_candidate_samples`（每候选最近 200 条或 30 天）
- `router_gate_approvals`

连接凭据使用本机 Fernet 主密钥加密。主密钥与数据库均位于
`server/model_router/storage/` 的持久目录并被 Git 忽略。未来迁移
PostgreSQL 时替换 repository 实现，不改变 service 或 API 契约。

严格预算要求候选同时提供输入和输出价格。调用前按预计输入与
`max_tokens` 上限预留，最终 usage 到达后按实际输入/输出 Token 结算；
失败则释放预留。缺失最终 usage 时不把费用展示为 0，也不标记为实际成本。

## 配置

```dotenv
MODELMIRROR_DEFAULT_TENANT_ID=local
MODEL_MIRROR_PROVIDER_ADMIN_PAIRING_SECRET=<至少 32 字符的外部 Secret>
MODEL_MIRROR_PROVIDER_INTERNAL_ALLOWLIST=new-api:3000
MODEL_MIRROR_CREDENTIAL_MASTER_KEY=<外部 Secret>
MODEL_ROUTER_CANARY_PERCENT=0
MODEL_ROUTER_ALLOW_NATIVE_OVERRIDE=false
MODEL_ROUTER_NATIVE_ALGORITHM=omniroute-parity-v2
```

运行方式通常由设置页持久化。`MODEL_ROUTER_ENGINE` 默认应为空：

```dotenv
MODEL_ROUTER_ENGINE=
```

只有紧急回退时显式设置：

```dotenv
MODEL_ROUTER_ENGINE=sidecar
```

显式 `sidecar` 会只读覆盖数据库中的运行方式；删除或留空并重建后端后恢复
设置页策略。`MODEL_ROUTER_ALLOW_NATIVE_OVERRIDE=true` 只供灾备或独立审计
使用，常规环境必须为 `false`。

只回退算法时设置 `MODEL_ROUTER_NATIVE_ALGORITHM=legacy`；它不删除 SQLite
或历史证据。完整引擎回退仍使用 `MODEL_ROUTER_ENGINE=sidecar`。

## 阶段门禁与回退

设置页允许：

- 稳定模式：全部 auto 请求走侧车。
- 对照观察：用户请求仍走侧车，同时记录一次本地决策。
- 本地试运行：按 `session_id` 稳定哈希执行 0–100% 灰度。
- 本地默认：当前算法至少 500 次、首末有效观测跨度 14 天、最近 24 小时
  有观测，并具有同期至少 100 条侧车对照后才进入其余门禁。

成功率必须不低于 98% 且相对侧车下降不超过 1 个百分点；空流与流中断合计
不高于 0.5% 且相对侧车增加不超过 0.25 个百分点；TTFT P95、每 100 输出
Token 的标准化 E2E P95、热缓存规划 P95 与硬约束违规分别执行固定门禁。
超时、429、5xx、空流、流中断、严格预算、连接停用和服务重启演练必须全部
通过。`PUT /api/router/gate/approval` 会先重新验证自动门禁，再把人工批准
绑定当前算法版本和配置哈希；权重或候选规则变化后旧批准自动失效。

紧急回退：

1. 先设置 `MODEL_ROUTER_NATIVE_ALGORITHM=legacy` 并重新创建 `server`。
2. 若仍需完整回退，设置 `MODEL_ROUTER_ENGINE=sidecar`。
3. 确认 `/api/router/status` 的 `engine` 为 `sidecar`。

```powershell
docker compose -p modelmirror up -d --force-recreate server
curl.exe http://localhost:8000/api/router/status
```

回退不删除 SQLite、连接、策略、用量、压缩或侧车数据。若侧车本身不可用，
普通模型聊天仍可用 `gateway=default` 走 newAPI/OpenRouter；auto 不做
跨网关静默回退。

## Harness 验收

```powershell
cd client
npm.cmd run build

python -m py_compile server/main.py
python -m pytest server/tests/ -q

docker compose -p modelmirror up -d --build --force-recreate
curl.exe http://localhost:8000/api/health
curl.exe http://localhost:8000/api/router/status
curl.exe http://localhost:8000/api/models/catalog
```

人工验收至少覆盖：

1. 首次连接无需理解网关术语，错误有下一步建议。
2. `/chat/auto` 六种策略的选择原因可理解。
3. 空流在正文输出前切换候选，输出后不切换；流式响应只有收到
   `[DONE]` 或明确 `finish_reason` 才算完成。
4. 严格预算超限返回 402，不发生跨网关回退。
5. 长上下文回执显示节省比例；保真失败显示未压缩。
6. 切回稳定模式后 default 聊天、RAG、Workflow、Expert Team 和 Xpert
   Runtime 均无回归。

### 流式截断经验

- 推理模型的思考 Token 与正文共用输出上限。`auto/*` 使用独立设置键，
  新会话默认上限为 8192，避免继承普通模型旧的 2048 配置。
- `finish_reason=length` 是达到用户输出上限，不计为普通成功；回执显示
  “回答达到最大输出长度”，用户可在高级参数中调整。
- HTTP 流自然关闭不等于模型完成。缺失 `[DONE]` 和 `finish_reason` 时按
  `stream_interrupted` 记录，保留已收到正文并提示重试，且不得在正文
  已输出后静默切换候选。
