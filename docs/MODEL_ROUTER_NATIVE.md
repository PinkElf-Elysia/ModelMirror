# 原生智能调度与上下文优化

最后更新日期：2026-07-28

## 当前状态

ModelMirror 已完成从 OmniRoute 侧车向本地原生能力迁移的阶段 0–4：

- 原生模型服务连接、目录归一、健康状态和加密凭据存储。
- 六种用户策略、硬约束过滤、稳定会话灰度、有限重试、熔断与 LKGP。
- Chat、RAG、工具输出和 Xpert Runtime 共享同一上下文优化内核。
- 严格预算调用前预留、最终 usage 结算、脱敏决策和压缩审计。
- 设置页提供普通用户可理解的连接向导、策略、上下文优化和折叠诊断区。

阶段 5 尚未完成，也不得通过修改数据伪造完成。只有同时达到至少 500 次
原生请求、连续观察 14 天、无 P0/P1、完成全部故障演练并通过人工验收，
才能把本地原生调度设为默认。当前默认和紧急回退路径仍是侧车；普通模型
聊天的 default/newAPI 路径始终独立。

## 2026-07-28 冻结决定

当前实现只获得初步本地验收，正式定级仍是“实验性原生候选”，不得表述为
稳定原生路由。OmniRoute 行为对齐进度冻结在本文记录的阶段 0–4 基线：

- 暂停新增原生路由、压缩、连接、预算或审计能力，不继续扩大对齐范围。
- 允许修复已发现的正确性、安全性、兼容性和数据完整性问题，并必须补充
  对应回归测试；缺陷修复不得借机改变评分权重或产品契约。
- 保留并维护 OmniRoute 侧车、`sidecar` 紧急回退和独立 default/newAPI
  路径，不删除镜像 profile、适配器、迁移记录或既有回退数据。
- `native` 默认门禁保持锁定。达到 500 次请求和 14 天仅是必要条件，不会
  自动获得稳定定级；仍需全面人工检验、故障修复和明确的恢复实施决定。
- 冻结期间的新迭代必须与 OmniRoute/原生迁移模块解耦，不得依赖
  `model_router` 的实验行为，也不得把实验入口替换为产品稳定入口。

只有项目维护人明确解除冻结后，才允许继续原生增量、提高灰度或讨论切换
本地默认。解除冻结应另建任务，重新检查基线、风险、回退和验收证据。

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

评分因子、默认权重和模式包来自固定 OmniRoute
`release/v3.8.49` / commit `36f8fd10052f` 的行为审计；ModelMirror 使用
Python 等价改写，没有复制上游控制台、存储或运行架构。固定向量位于
`server/model_router/fixtures/omniroute-v3.8.49-routing.json`。

硬过滤顺序覆盖租户、连接启用、凭据、熔断、输入输出模态、工具能力、
上下文长度和严格预算。能力、权限和预算永不 fail-open；非必要类别偏好
可以回到完整合格池。失败最多切换两个候选，且只允许在尚未向用户输出正文
前切换。空流、零 Token 且无正文、只有 `[DONE]` 均按失败处理。

熔断按租户、连接和模型隔离：

- 连续三次失败进入 `open`。
- 冷却后进入 `half_open`，只作为探测候选。
- 成功关闭熔断；失败延长冷却时间，最长 30 分钟。

Shadow 只保存原生决策，不发送第二次模型请求，不增加费用。
`auto` 会用最新用户消息做本地、确定性的高置信任务标签判断；只有明确的
代码、报错、证明或多步推理信号才增加对应类别偏好，不发送分类模型请求，
也不保存原始消息。无明确标签时优先通用对话模型，避免代码、Embedding、
Rerank 等专用模型因低价被误选；若没有通用候选则按软偏好规则回到完整
合格池。`auto/coding` 等显式入口始终优先于该轻量判断。

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

路由回执 v2 只展示实际模型、连接、策略、成本种类、请求 ID、预算状态和
压缩摘要。详细原因码与阶段数据默认折叠，不返回评分矩阵、密钥或完整
Prompt。

## 存储与预算

SQLite 当前 schema 版本为 3，表固定为：

- `router_connections`
- `router_policies`
- `router_candidate_stats`
- `router_decisions`
- `compression_runs`

连接凭据使用本机 Fernet 主密钥加密。主密钥与数据库均位于
`server/model_router/storage/` 的持久目录并被 Git 忽略。未来迁移
PostgreSQL 时替换 repository 实现，不改变 service 或 API 契约。

严格预算要求候选同时提供输入和输出价格。调用前按预计输入与
`max_tokens` 上限预留，最终 usage 到达后按实际输入/输出 Token 结算；
失败则释放预留。缺失最终 usage 时不把费用展示为 0，也不标记为实际成本。

## 配置

```dotenv
MODEL_ROUTER_TENANT_ID=local
MODEL_ROUTER_CANARY_PERCENT=0
MODEL_ROUTER_ALLOW_NATIVE_OVERRIDE=false
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

## 阶段门禁与回退

设置页允许：

- 稳定模式：全部 auto 请求走侧车。
- 对照观察：用户请求仍走侧车，同时记录一次本地决策。
- 本地试运行：按 `session_id` 稳定哈希执行 0–100% 灰度。
- 本地默认：500 次与 14 天自动门槛达标后才可选择，仍需人工验收。

阶段升级必须依次完成空流、超时、429、5xx、预算、连接停用和服务重启
演练。数据库记录是观察证据，不是自动批准；无 P0/P1、完整回归和人工
验收属于独立手工门禁。

紧急回退：

1. 设置 `MODEL_ROUTER_ENGINE=sidecar`。
2. 重新创建 `server`。
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
