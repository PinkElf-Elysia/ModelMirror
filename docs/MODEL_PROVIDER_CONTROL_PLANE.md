# Model Provider Control Plane

状态：Round 0–4、Round 5A–5D 已合并；Round 5E 正在建设资格证据与 required 人工门禁；默认数据面切换未批准
Round 5E 当前基线：`origin/main@7e543d8c02dbb0386771bcae052d38f78c33b9ce`
更新日期：2026-08-22

## 决策

ModelMirror 建设单租户 Model Provider Control Plane，统一管理动态 Provider
连接、凭据、策略、诊断和安全出口。首期主体固定为 `tenant_id=local`，但认证、
Service 与 Repository 接口继续保留租户边界。

newAPI 是独立、未修改的 OpenAI-compatible 数据面，不是 ModelMirror 前端模块。
ModelMirror 不嵌入、代理、复制或修改 newAPI 管理界面，只允许配置一个外部管理
链接。newAPI 使用独立 Compose 项目、数据目录和生命周期；核心栈只通过显式
URL/Key 契约连接。上游采用 AGPLv3 并包含附加条款，实际部署者仍需独立完成
许可证与使用场景审查。

Coding Runtime 继续使用自己的 Provider URL、Key、网络和审批边界，不读取
Provider Control Plane 的会话、SQLite 或主密钥。它只加入独立外部
`coding_provider` 网络，不加入控制面/newAPI 的 `provider` 网络。

## 授权与门禁

- 本决策解除 2026-07-28 对原生路由继续建设的功能冻结，仅批准控制面、安全与
  兼容建设。
- `native` 默认仍需至少 500 次真实请求、连续 14 天、无 P0/P1、故障演练和人工
  验收；数据不能被修改来伪造门槛。
- Round 0–1C 不改变 `/api/chat`、SSE、公开模型目录或默认路由协议。
- Round 2 统一普通文本 Chat 的内部调用契约，但不改变默认 Provider、公开请求协议、
  SSE 或路由回执，也不产生影子复制流量。
- Round 3 只增加用户显式、页面会话级的 `newapi_canary`；默认关闭，不做百分比灰度、
  影子复制、自动切流或默认资格判定。
- Round 4 统一模型发现、Readiness 投影和 Provider 设置入口，不统一各模态调用协议，
  不触发自动刷新、自动认证、自动路由或付费请求。
- Round 5A 只增加 Managed Chat 的逐能力认证、稳定策略、资格和脱敏 Receipt 基础；
  不接管 `/api/chat`。真实调度从 Round 5B 开始并需独立验收。
- Round 5B 只迁移稳定白名单内的 default 普通文本与已提取文本附件；Round 5C 只为
  Auto 接入不改变选路的独立证据管道。
- Round 5D 只迁移 MCP 工具和受控文件输出的 Provider 选择与 Receipt；工具执行、权限、
  文件渲染和专用多模态协议仍由既有 Runtime 负责。
- Round 5E 只实现普通文本 required 资格纪元、证据聚合、故障降级和可审核人工激活；
  代码合并、部署或自动门槛达标均不等于批准切换。
- newAPI 是否成为强制默认数据面由后续独立决策决定。
- 任一门禁失败时保留现有静态数据面和 SQLite，不自动迁移或删除数据。

## 部署边界

- 核心栈：`docker-compose.yml`，能够在 newAPI 未运行时达到基础健康状态。
- newAPI 栈：`deploy/newapi/compose.yml`，使用固定镜像 digest 并复用现有
  `new-api-data`。
- 互联：`deploy/newapi/modelmirror-overlay.yml`，只让 Server 加入显式共享网络并注入
  URL；Coding Runtime 不加入该网络。
- newAPI 栈必须先启动；核心 Compose 不使用跨项目 `depends_on`，也不接管其升级、
  停止或数据恢复。

## 管理会话与动态出口

- `POST/GET/DELETE /api/router/admin/session` 使用外部注入、至少 32 字符的配对密钥；
  服务端只保存 32-byte 随机会话令牌的哈希，会话绝对有效期 8 小时并随重启失效。
- Cookie 限定 `HttpOnly; SameSite=Strict; Path=/api/router`；非 HTTPS 仅允许 loopback，
  写操作还必须提供内存中的 `X-ModelMirror-CSRF`。
- 动态 Provider 的公网目标仅允许 HTTPS；内网 HTTP(S) 必须精确加入
  `MODEL_MIRROR_PROVIDER_INTERNAL_ALLOWLIST=host:port`。
- 每次请求重新解析全部 A/AAAA 结果；混合公共/私有解析整体拒绝。实际连接固定到已批准
  IP，同时保留原始 Host 与 TLS SNI，不跟随重定向且不读取代理环境变量。
- Metadata、link-local、multicast、unspecified 与保留地址永久禁止，白名单不能覆盖。

## 主密钥与显式迁移

密钥优先级固定为：测试构造参数、`MODEL_MIRROR_CREDENTIAL_MASTER_KEY`、弃用的
`MODEL_ROUTER_CREDENTIAL_MASTER_KEY`、开发模式本地 `credential-master.key`。启用
`MODEL_MIRROR_REQUIRE_EXTERNAL_CREDENTIAL_MASTER_KEY=true` 后只接受规范变量；缺失时
锁定动态控制面，不回退旧变量或本地文件。

变更密钥必须显式运行：

```powershell
python -m server.model_router.migrate_credentials --storage-dir <path>
```

命令先全量解密预检，再使用 SQLite Backup API 创建时间戳备份，在
`BEGIN IMMEDIATE` 中重加密并逐条回读验证，最后写入密钥指纹。失败会 Rollback；旧
数据库备份和本地旧密钥文件不会被删除。回退时恢复备份并重新启用旧密钥来源。

## 文本 Chat 契约与 newAPI 认证

- 内部契约版本为 `modelmirror-provider-chat-v1`。Static、OmniRoute sidecar 与 Native
  managed target 使用相同的 Base URL、`/models`、`/chat/completions` 解析规则。
- Managed target 在真实请求前继续执行 DNS 解析、地址审批与 IP pinning；Static 与
  sidecar 仍是可信部署输入，不被动态内网白名单误阻断。
- newAPI 认证只验证核心文本 Chat 兼容性。它不证明多模态、RAG、Workflow、Agent、
  Coding 或默认数据面资格，也不计入 Native Router 的 500 次/14 天门禁。
- `POST /api/router/connections/{id}/models/refresh` 刷新可认证模型；认证 POST 要求管理
  会话、CSRF、`Idempotency-Key` 和显式费用确认。
- 每次认证先刷新模型目录，再发送最多一个固定合成请求；付费 POST 不重试、不轮换 IP、
  不跟随重定向。SQLite v12 只保存脱敏检查、指标和配置指纹，不保存 Prompt、模型正文
  或凭据。
- `MODEL_MIRROR_PROVIDER_CHAT_CERTIFICATION_ENABLED=false` 可停止新的认证操作，不影响
  连接管理、现有认证记录或默认数据面。
- 认证默认有效 24 小时，可通过
  `MODEL_MIRROR_PROVIDER_CHAT_CERTIFICATION_MAX_AGE_SECONDS` 在 300 到 2592000 秒范围内
  调整。过期、非法 TTL 或无法解析的完成时间都会让 Canary 失败关闭；系统不会自动发起
  可能计费的重新认证。
- 连接的 Base URL、类型、scope 或凭据变化会让旧结果派生为 `stale`；名称或健康检查
  时间变化不会。Server 重启会把遗留 `running` 标为 `uncertain`，不会自动重放。

## 手动会话 Canary 与证据

- `MODEL_MIRROR_PROVIDER_CHAT_CANARY_ENABLED` 默认 `false`。关闭只禁用手动 Canary，
  不影响连接、认证或当前 default/OmniRoute/Native 数据面。
- 管理员通过 `GET/PUT /api/router/canaries/chat` 选择唯一 newAPI 连接；启用前必须存在
  当前连接指纹下至少一个逐模型 `passed` 认证。Settings 只显示脱敏状态、指标与暂停
  原因，不提供比例灰度或默认切换控件。
- Chat 页通过公开只读、`Cache-Control: no-store` 的
  `GET /api/models/provider-chat-canary?model_id=<exact-id>` 判断精确模型资格。开关默认
  关闭且只保存在页面内存；切换模型或刷新页面后重新确认，开关本身不调用模型。
- `gateway=newapi_canary` 必须提供页面 `routing.session_id`，并只接受纯文本历史、
  `tool_mode=none`、`output_mode=none`，以及无 Skill、文件、图片、音频或视频的请求。
  temperature、top_p、seed、stop、max_tokens 与纯文本 prompt suffix 保持现有契约。
- 预检失败可在 Canary POST 发出前回到当前默认路径，并回执
  `engine=newapi_canary_fallback`。一旦受管 Transport 标记 `dispatched`，只连接一个已批准
  IP、只发送一个 POST，之后不得切换 IP、连接、模型或默认 Provider。
- 成功流原样转发，同时旁路观察正文是否出现、终止、TTFT/E2E 和可用 usage；证据不保存
  用户消息、模型正文、完整上游错误或凭据。回执使用 `engine=newapi_canary` 与
  `strategy=explicit_session`，不公开连接或数据库运行 ID。
- SQLite v13 新增租户级策略和运行证据表。遗留 `running` 在重启后变为 `uncertain`，
  绝不重放。401/402/403/404、非法 SSE、空流和缺少终止会立即按连接+模型暂停；429、
  5xx、连接/读取超时或流中断连续三次后暂停；请求特定 4xx 和客户端取消不触发暂停。
  新的同模型真实认证通过后，以新 certification ID 开始新的失败窗口。
- Settings 将当前连接指纹与最新有效认证下的近期记录单独汇总；旧指纹、旧认证窗口或
  过期认证下的运行保留为明确标记的历史证据，不计入当前成功率。该汇总只覆盖接口返回的
  最近记录，不能解释为全量历史或默认数据面资格。
- 受管端点与当前静态默认端点相同时标记 `baseline_overlap`；该运行只证明受管路径，不能
  作为未来默认资格证据。Round 3 已在明确额度授权后完成一次真实人工验收；后续每次
  真实额度 Canary 仍需单独授权和人工验收。

## 统一 Catalog、Readiness 与设置入口

- 内部与公共契约版本为 `modelmirror-provider-catalog-v1`。`CatalogModel`、连接级
  `ProviderInventoryRecord`、逐 operation 的 `ProviderOffering` 与只读
  `OperationReadinessProjection` 分离；不得按模型名称推断 operation、价格或兼容性。
- SQLite v14 加法新增刷新、Inventory 和 Offering 表，所有唯一键包含 `tenant_id`。
  完整未截断刷新才会退休未再次出现的旧模型；失败或截断只将旧证据标为 stale，
  不删除最后一次成功目录。遗留 `running` 在重启后变为 `uncertain`，不会重放。
- `POST /api/router/connections/{id}/catalog/refresh` 只执行显式模型目录 GET。连接健康、
  Inventory、Offering 与刷新证据在同一事务提交；旧 `/models/refresh` 委托同一服务并
  保留原有最多 500 个 ID 的响应契约。
- 公共 `GET /api/models/control-plane-catalog` 按模型、operation 和 access mode 返回
  目录出现、连接状态、认证、Canary 与专用数据面的聚合证据。响应不包含 tenant、连接
  ID、Base URL、凭据或内部错误，并设置 `Cache-Control: no-store`。
- Readiness 是只读现状投影。Chat 认证只证明 Chat；Canary 只增加
  `newapi_canary` access mode；任一可调用 Offering 可以令 operation 可调用，但冲突、
  过期与失败证据仍通过稳定 reason code 保留。价格只是带来源的十进制字符串元数据，
  `billing_authoritative=false`，冲突报价不求平均。
- `/settings` 使用 `?section=overview|providers|routing` 组织总览、Provider/Catalog 与
  路由实验。三页签共用管理员会话和 CSRF；Marble 位于控制面之外，即使未配对仍可用。
  newAPI 管理界面仍只允许安全外链，不嵌入、代理或自动加载。
- 公共 `/models` 与 Prompt 目标模型选择器继续使用原有静态快照口径，不显示或断言运行时
  `invocable`，也不插入仅由 Provider 发现的模型。统一 Readiness 仅在设置页审计，避免
  控制面建设改变既有用户模型浏览与选择体验。
- R4 受管 Provider 不会自动配置或接管普通 `/api/chat`。当前 default Chat 仍读取
  `LLM_GATEWAY_URL/KEY` 或 `OPENROUTER_API_KEY`；迁移和 newAPI 默认门禁属于 Round 5。

## Managed Chat 策略、稳定路由与 required 门禁（Round 5A—5E）

- 内部契约版本为 `modelmirror-provider-chat-routing-v1`，将 `chat_text`、
  `chat_tools` 和 `chat_file_output` 分别认证和配置；普通文本认证不能证明工具或受控
  文件输出兼容。
- Chat 认证扩展到所有启用且带 `chat` scope 的 Managed Provider。工具认证只请求固定、
  无副作用的合成工具且不执行；文件输出认证只验证 allowlisted 合同且不保存生成内容。
- SQLite v15 以纯加法保存租户策略、有序能力路由、资格快照、逻辑运行、Provider 尝试、
  Gate 纪元与脱敏验收证据。记录不包含 Prompt、消息、模型输出、Base URL 或凭据；遗留
  `running` 在重启后标为 `uncertain` 且不重放。
- `GET/PUT /api/router/chat-control/policy` 使用 `expected_revision` 原子更新，避免覆盖并发
  管理修改。`GET /api/router/chat-control/gate` 和 receipts 接口提供只读门禁与脱敏运行
  证据；普通策略更新不能直接进入 required。
- R5B 只接管 `gateway=default`、稳定模型白名单内的普通文本和已提取文本附件。
  `MODEL_CONTROL_CHAT_ENABLED` 默认 `false`；关闭开关、选择 `legacy` 或使用白名单外模型
  时继续走原有静态路径。
- `newapi_preferred` 的首选 newAPI 只有在 POST 派发前的资格、目录、凭据或 DNS/SSRF
  预检失败时才可选择显式 Managed 备用。POST 派发后不重试第二 IP，不切换备用、模型或
  legacy Provider；逻辑运行和逐 Provider 尝试均写入不含请求/回答正文的 Receipt。
- R5C 不改变 Auto 的实际选路和重试语义，只在 `MODEL_CONTROL_CHAT_ENABLED=true` 且
  租户策略 `auto_enabled=true` 时为普通文本 Auto 写入统一 Receipt。Native 每个真实
  Provider 目标独立记录 attempt；OmniRoute 只记录 ModelMirror 可见的一次 sidecar
  attempt，内部重试标记 `provider_attempts_not_observed`。Auto 记录始终设置
  `primary_newapi=false`，不计入 R5E required 门禁。
- R5D 将 `gateway=default` 的 MCP 工具模式和 `output_mode=allowlisted` 分别接入
  `chat_tools` 与 `chat_file_output`。两条路径都必须使用同一精确模型的独立当前认证；
  缺失资格时在派发前阻断，不能借用 `chat_text` 或静默进入 legacy。
- 工具由 ModelMirror Runtime 执行，文件由 allowlisted renderer 校验；Provider 只接收
  既有安全模型输入，不接收 MCP 凭据或本地文件权限。需要多次模型决策时保持同一连接和
  已批准 IP，策略漂移则失败关闭，不跨 Provider 重放。
- R5E 只把当前资格纪元内真实用户发起、`gateway=default`、`chat_text`、首选 newAPI
  实际派发的稳定模型请求计入 required。Canary、认证、Auto、工具、文件、预检备用和
  客户端取消不计入；每个稳定模型至少需要 10 次成功样本。
- 自动门槛固定为至少 500 次合格请求、首末跨度至少 14 天、总成功率至少 99%、零硬
  失败。达到门槛后仍需管理员确认无未解决 P0/P1、全部故障演练、required 失败关闭语义，
  并提供 newAPI 额度扣减、Token 日志关联及重启持久化的有界验收结论。
- `POST /api/router/chat-control/gate/activate-required` 使用策略 revision 在同一事务中复核
  资格并记录人工批准。验收关联引用只保存 SHA-256 哈希；不保存余额、完整 newAPI 日志、
  Prompt、回答或引用原文。该记录证明外部数据面验收，不构成 ModelMirror 计费系统。
- required 只使用有序文本路由的首选 newAPI。任何预检或派发后失败都失败关闭，不能调用
  备用、第二 IP 或 legacy；401/402/403、模型不一致、非法/空 SSE 或缺少终止信号会立即
  关闭资格纪元、撤销批准并标记 degraded，但策略保持 required，不会自动降级。
- degraded 后管理员只能显式退回 preferred，重新认证并建立新纪元；相同失败纪元不能被
  自动重开。瞬时失败计入成功率，但不自动关闭纪元。
- 清理命令 `python -m server.model_router.cleanup_chat_receipts --storage-dir <path>` 默认
  dry-run；只有显式增加 `--apply` 才会删除超过保留期的运行和尝试记录，默认保留 90 天。

## Agent 与 Workflow Managed 控制面基础（Round 6A）

- 内部契约版本为 `modelmirror-provider-workload-routing-v1`。入口、执行形态、精确模型与
  单一 Managed Connection 组成 Binding；客户端不能提交连接 ID，也不能按模型名称推断能力。
- SQLite v16 纯加法保存 Workload 资格、入口 Policy、Binding、批准、父运行与逐逻辑调用
  Receipt。所有约束包含 `tenant_id`；记录不包含 Prompt、消息、模型输出、工具参数、Base URL
  或凭据。遗留 `running` 在重启后变为 `uncertain`，绝不自动重放。
- `chat_text` 与 `chat_tools` 复用 R5 对应能力认证。R6A 新增 `chat_text_unary`、
  `chat_json_object` 与 OpenRouter 专用 `fusion_native` 认证；真实认证只允许一个已批准 IP、
  一个 Provider POST 和零自动重试，合成输入及响应正文不落库。
- 管理接口以 optimistic revision 原子维护 Policy/Binding，并提供脱敏资格、Overview 和
  Receipt 查询；公共状态只返回部署开关、状态、是否在派发前阻断及稳定 reason code。
- R6A 不接管任何 Agent、Workflow 或 Xpert 数据面。所有 R6 Feature Flag 默认 `false`。
- R6B 只接入 Engine Shadow：模型别名解析为精确 invocation ID 后，使用
  `agent_shadow + chat_tools` Binding；Worker `model.request` ID 是不可重放的逻辑调用键。
  Flag 关闭或 Policy 为 `legacy` 时保留原 Shadow 网关；已激活策略失效后保持
  `degraded_required` 并失败关闭。
- R6C 接入 `/api/meta-agent/generate-workflow` 与
  `/api/meta-agent/generate-xpert-candidate`。两者使用 `chat_json_object` 精确 Binding；规划、
  蓝图和最多一次修复分别记录调用序号。派发后不重试、不换 IP、连接、模型或 legacy；响应
  只加法返回运行引用、模型、调用序号、状态和可用 usage，不返回内部连接或生成正文。
- R6D 接入受信任的 Classic Workflow 交互与部署执行。`llm`、参数提取和分类模型回退分别
  使用 `chat_text`、`chat_json_object` 和 `chat_text_unary` 精确 Binding；规则命中不创建
  Provider 调用。每个模型节点以入口、Workflow task/部署 execution 和 node ID 形成稳定运行，
  自动恢复遇到既有派发或不确定证据时失败关闭，显式新执行才可创建新的可计费调用。
  缺省 `modelId` 在 Managed 模式规范化为现有 `TEXT_FALLBACK_MODEL`，仍须具备该精确模型的
  Inventory、资格和 Binding。Provider 失败不再返回旧 `{}` 或默认分类；legacy 模式保持原行为。
- R6E 接入 Classic Workflow 的 `agent` 与 `workflow_agent`。交互和部署使用独立 Policy；
  直接回答/ReAct、Function Calling、结构化输出及中间模型调用分别要求 `chat_text`、
  `chat_tools`、`chat_json_object` 精确 Binding。`auto` 在派发前按资格确定策略，Managed 模式
  禁止 `retryOnFailure`、`fallbackModelId` 和 Function Calling 失败后转 ReAct。HITL 恢复复用
  已保存输出，只有显式 revise 创建新的稳定阶段和模型轮次。工具调用仍由 Runtime 执行，Receipt
  不保存工具参数、Prompt 或模型正文。
- R6F 接入直接 Published Xpert 与已部署 Xpert App，分别使用 `xpert`、`xpert_app` Policy。
  工作流内文本、工具与结构化轮次复用 R6E Call Service；Xpert Chat 和 App 响应只增加脱敏
  Receipt，不改变回答正文或会话历史。受控子 Xpert/Handoff 显式继承 `xpert` 上下文，Managed
  失败不自动重放；Automation、Goal、评测、进化、记忆候选、后台增强与 Skill 流程保持排除。
  文件仅使用既有抽取文本，多模态继续专用 Adapter。
- R6G 接入 Expert Team Planner 与 Agency DAG。Planner 使用 `chat_text_unary`；DAG 的普通
  执行调用使用 `chat_text_unary`，JSON 验收/裁判使用独立 `chat_json_object`，两类 Binding
  都必须匹配精确模型与当前连接指纹。Worker request ID 直接成为逻辑调用键；初始、HITL
  恢复、续跑和返工以独立稳定片段记录 Receipt。请求开始时冻结控制模式，managed 请求派发前
  资格漂移或策略停用只会失败关闭，派发后失败不得换连接、模型、IP 或 legacy。Worker 环境、
  API、SQLite、日志和浏览器不保存 Key、Prompt 或模型正文。
- R6H 接入 Fusion。原生模式只使用精确 `openrouter/fusion` 的 `fusion_native` Binding，
  运行时有序候选与裁判必须匹配当前资格 Profile；失败后不再自动转应用层。应用层模式在首个
  POST 前一次性预检全部候选和裁判 `chat_text` Binding；候选与裁判分别记录计划调用，候选
  部分失败可继续其余已计划调用，但不切换 Provider。裁判失败不会调用备用模型，也不会把候选
  正文伪装为裁判结果。两种模式互斥，legacy 只在 Flag 关闭或管理员显式停用 Policy 后恢复。
- R6I 接入 Route Agent 与 Team Chat。专家匹配继续使用本地索引，Route 最终作答使用一次精确
  `chat_text` 调用；Team 在首个 POST 前一次性预检全部成员轮次和最终汇总，计划调用数固定为
  成员数加一。任一 Managed 调用失败后不再切换 `TEXT_FALLBACK_MODEL`、第二连接、第二 IP 或
  legacy；未派发的剩余 Team 调用写入失败证据。Flag 关闭或管理员显式停用 Policy 时，两个
  入口继续保留原 legacy 行为。
- 所有 R6 入口的每个逻辑调用只能派发一次；Provider Key 只能存在于 Python 主进程内存，
  Worker、工具执行器和浏览器不得收到 Key、URL 或连接细节。
- Receipt 清理命令现在同时检查 R5 Chat 与 R6 Workload 记录，仍默认 dry-run；`--apply`
  才删除超过保留期的已完成运行、尝试或调用。

## 回退

关闭管理面或回退路由不得删除 Provider SQLite、newAPI 数据目录、旧主密钥或迁移
备份。将 `MODEL_MIRROR_PROVIDER_CHAT_CANARY_ENABLED=false` 可立即关闭 Round 3 入口；
将 `MODEL_CONTROL_CHAT_ENABLED=false` 保持 R5 数据面关闭。关闭各入口对应的
`MODEL_CONTROL_*_ENABLED` 可保持 R6 数据面为 legacy。代码回退保留 v15/v16 表和
脱敏证据；只需将策略 `auto_enabled=false` 即可停止新增 Auto 证据而不改变 Auto 调度。
旧版本可忽略新表继续运行。部署回退通过恢复上一版本
Compose 与对应显式环境配置完成。
