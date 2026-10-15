# R9C1 来源与覆盖复审

## 99e50f99 发布前复审

2026-10-15 按用户提交授权刷新并整合 `99e50f99e341a013c4c826d623d2e75a26483088`。20 个上游路径已核对，直接交叉为 main 的 Decisions 枚举与帮助索引；关联 STT Profile、WAV 输入检查和目录资产已纳入当前回归。没有新增发送位置；81 个分类入口、947 个静态候选保持不变，ScaleDown 仍为待迁移，不借来源适配声明全覆盖。

当前源码的交叉回归 78 项、Shadow/Worker 36 项通过。前端全量 1262 通过、3 项目录断言失败，均在干净同 SHA、同依赖复现并逐项获得本批豁免；不继承 ec79 的旧豁免。独立 15169 合成恢复、再次重启、帮助搜索重放与用户截图归档完成。后端全量最终为 10668 通过、37 跳过、0 失败，另有 45 个子测试通过；拆分的 Workflow 合同实际通过 8 项。37 个跳过 ID 及专用环境原因已逐项核对，Shadow 由本次独立补测覆盖，其余 36 项不计通过。最终暂存审查未发现新的可确认阻断缺陷；本地门禁包含三项明确豁免，不等于自动全绿或生产验收。本节写于 Commit、Push 和 PR 前。证据及三项精确豁免见 [C1 验收记录](provider-control-r9-c1-closeout.md)。

## 2026-10-15 ec79e120 历史门禁状态

用户批准固定依赖复用后，`ec79e120` 新源码已独立重建并逐文件核验。证据目录 `C:/tmp/r9c1-acceptance-ec79-offline-20261015`；七个冻结阶段通过，后端全量最终为 10659 passed / 37 skipped / 0 failed，另有 45 个子测试通过。前端 1261 passed / 1 failed，唯一目录断言经同 SHA 干净窄测试复现，用户只豁免这一项。Node 补测 17 passed。所有原失败报告保留，不把人工豁免等同于全绿。

帮助资产的第二项基线问题没有沿用上述豁免：用户另行授权最小 PNG 格式修复，原 JPEG 保留，宿主及固定镜像资产复测通过。只涉及图片编码、引用和证据记录，没有改变 ScaleDown 或 C1 调用实现。详细哈希及冻结后差异见任务卡和 `docs/help-center/openrouter-oct13-preview.md`。

15168 新合成预览的真实启动恢复和再次重启均符合预期：有来源的零模型任务完成，缺来源任务失败关闭，调用表仍为零。帮助搜索和处置文字已浏览器核对；用户提供的新截图已原样归档到 `docs/help-center/evidence/provider-context-r9c1-ec79e120/`，旧 15167 截图不计入本基线。帮助资产增量镜像只更新客户端，PNG 已在浏览器确认加载完成；Server 和卷未重建。Shadow/Worker 的当前冻结源码补测为 36 passed，使用真实 Node Worker、合成模型回调，不证明真实 Provider 可用。

37 项跳过分为 Shadow Worker 1 项、Windows 原生行为 16 项、Coding 专用语言服务器 5 项、Office/MCP 专用环境 15 项。Shadow 项已在当前补测中通过；其他 36 项不计为通过，不据此宣称相应专用环境可用。

逐文件比对确认冻结后的差异仅为获批帮助图片、文章引用及审计归档，没有运行时代码或测试差异。关键来源创建、继承、恢复、事务派发、认证与桥接差异审查未发现新的可确认阻断缺陷；有限秘密模式扫描、差异格式和三组 Compose 配置检查通过。原冻结包总校验仍退出 1，需结合已批准的目录断言豁免与独立帮助资产修复证据阅读。

本地验收与归档已完成，正式提交前仍需刷新上游并核对交叉变化。回滚仅有受控兼容 Repository 验证，完整 Server/容器回滚及真实 Provider 不在本次通过结论内。没有 Commit、Push 或 PR。当前证据及待发布说明见 [C1 最终验收记录](provider-control-r9-c1-closeout.md)；下文保留历史审查过程。

## 获批的范围处置与整合

2026-10-14 用户批准后，工作树已快进至 `ec79e1203c0f26e9efa20e4e75e7e0bf9de2a795` 并恢复 C1 修改。冲突前快照和 stash 保留；当前清单为 81 个分类入口、947 个候选，新增 ScaleDown 只登记为 `migration_pending`，后续纳管另批决定。原有入口状态没有晋级，运行开关没有启用。

新增出口的实际链路为 `ScaleDownPage → POST /api/scaledown → create_scaledown_result → Chat Completions`。它没有统一上下文、资格、Binding、Receipt 或持久幂等，超时没有自动重试但结果仍未知。登记如实保留这些缺口，不将普通非流式协议直接认作合格的 `chat_text_unary`，也不扩展 C1 实现范围。

清单冲突保留最新主线已审查的 `plugin-host` 为 `model_delegate/manual_harness`，真实工具仍为 `rpg_bridge`；这纠正此前 C1 对通用注入回调范围偏宽的关联，不改变代码或授权。TTS 新增声音、固定语速和 44.1 kHz PCM，以及音频/图片目录筛选，只更新已核对的源码指纹，不能替代资格认证。新基线回归结果以任务卡最新记录为准。

## 2026-10-14 复核结论

`2df7efba` 加 C1 的最终冻结验收九阶段通过；后端 10614 passed / 37 skipped / 0 failed，C1 相关部署 Worker 补测 36 passed / 0 skipped。新独立 15167 预览已验证合成来源恢复、缺来源阻断、第二次重启不新增运行，以及帮助搜索和处置指引。完整哈希、命令、跳过项与回滚限制见任务卡最新一节；不把 Mock、无模型节点或页面说明视为真实 Provider 验收。

发布前再次 Fetch 后上游为 `ec79e1203c0f26e9efa20e4e75e7e0bf9de2a795`，尚未整合。直接交叉为 main、帮助索引和出口清单，关联变化包括模型准入及 TTS Profile/PCM。新 `/api/scaledown` 是直接 Provider POST，未建立 C1 来源/Receipt，最新上游清单也没有该入口登记。不能把现有 80 入口/944 候选或旧 SHA 绿测当作新主线覆盖证明。

建议登记新入口为 `migration_pending`，另行决定纳管批次，不扩张 C1 或假借现有资格。本轮只归档既有验收及该范围发现；提交门禁仍等待新基线整合回归、同基线预览/截图归档和最终审查。没有提交、推送或创建 PR。

## 范围和状态

复审对象为 `codex/provider-control-r9-c1` 的未提交修改。2026-10-13 获准整合后 Fetch，HEAD 已更新至 `2df7efbaa1e91bfc625cf6e9bb5a10e0162ab0cc`；整合前快照和 stash 保留。本文记录源码覆盖和本地合成测试，不表示已部署、已启用、真实 Provider 已验收或具备账户/计费功能。

覆盖清单为 80 个分类入口、944 个静态候选点（整合前为 935）。C1 对应 37 个既有 Workload、Chat default/Auto/Canary、两个服务桥接及认证。其余 `migration_pending`、`external_domain` 和 `excluded` 分类不变。静态扫描包含普通 HTTP、配置读取、委托及目录文字误报，候选数不是模型 POST 数。

### 新主线交叉核对

- 恢复租约与可信来源分别验证，不互相替代。测试恢复必须真实 mark_ready/claim，不能把无租约快照直接送入 Runtime。
- 客户端等待的新逻辑键同时保存来源与旧键重放保护；114 项交叉回归覆盖来源、Managed 预算、Handoff 预算和结算。旧基线全量不继承为新 SHA 的通过结论。
- God-card 九个新增静态候选：静态资源服务、导航头、离线 Profile 和代理属于非模型 HTTP；`plugin-host` 的生成回调及 `real-first`/`real-preview` 的真实请求工具登记为 RPG 模型委托。真实工具仍需其独立授权，本次未执行。
- 上游 RPG bridge 新增 `god-long-v1` 参数 Profile 和派发前失败 Receipt；仍经现有 scoped bridge。目录新增模型与专家只涉及数据，音频目录新增 Profile 不等于真实资格。工具宿主的 dispatch_count/缓存键和 Sandbox 排序调整不成为 Provider 模型出口。
- 不改 RPG、Office、Client Extension 的业务实现或外部执行域；只更新已审查候选指纹。未审查路径仍使覆盖检查失败。

## 可信来源与恢复矩阵

以下测试路径均位于 `server/tests/`；持久来源由 `server/model_router/call_context_storage.py` 管理。业务输出仍由各 Runtime/Adapter 负责，公开 Receipt 不包含内部身份对象。

| 入口组 | 创建和继承 | 恢复边界 | 合成证据 |
| --- | --- | --- | --- |
| Chat default、Auto | 本机匿名主体，服务端 run/attempt、策略 revision | 新服务不能借旧对象派发；Auto 保留原逐次尝试语义 | `test_provider_call_context_chat.py`、`test_provider_call_context_auto.py` |
| Canary | 本机匿名主体，持久策略版本、session 哈希 | 缺旧版本须显式保存；缺来源不可再派发 | `test_provider_context_canary.py` |
| RPG bridge | 已鉴权 S2S 请求，`service/rpg-bridge`；限定模型与参数仍由原桥接校验 | session/request 占位不重放；来源不从客户端字段读取 | `test_rpg_bridge.py`、`test_provider_call_context_chat.py` |
| AI Research bridge | 普通/P2R 独立服务授权；`service/ai-research-bridge` | 文献与 scoped 路由均使用持久来源；不将服务请求假定为后台辅助 | `test_ai_research_bridge.py`、`test_provider_call_context_chat.py` |
| Meta、Fusion、Route Agent、Team Chat | 各自固定 request source，本机匿名主体 | 每个已计划调用分别保留来源，不转成后台或另一入口 | `test_provider_context_request_adapters.py` |
| Classic Workflow 交互/部署 LLM、Agent、Vision | 交互本机主体；部署固定服务主体；宿主 task/execution/node 引用 | 来源、任务和部署引用复核；未决调用不重放 | `test_provider_context_workflow.py`、`test_provider_context_runtime.py` |
| Published Xpert、App、Vision | 独立 source；受控子 Xpert/Handoff 固定父来源 | 子任务稳定键跨父恢复保持一致；缺父来源阻断 | 同上，以及 `test_xpert_handoff_executor.py`、`test_workflow_resource_nodes.py` |
| Shadow | 宿主 Shadow Run ID；Worker 不提供身份 | 宿主映射须与持久来源精确对应，不能借另一合法运行 | `test_provider_context_shadow.py` |
| Expert Team Planner、DAG | Planner request、Agency 宿主 task 分开；子任务继承 | HITL/续跑复核当前策略，uncertain 不重放，不借旧 legacy 恢复绕过 | `test_provider_context_expert_team.py`、`test_provider_context_worker_runtime.py` |
| RAG 生成、Processor、Embedding、Rerank、Vision | request/service/evaluation/inherited 来源分开；Job 来源写入 Router | 旧 Job 不补造主体；查询子调用验证 Workflow/Xpert 父链 | `test_provider_context_rag.py`、`test_provider_context_rag_runtime.py` |
| Skill Rerank | request/service/evaluation/inherited 来源分开 | 保留 Shadow/晋级治理；工具调用只继承宿主来源 | 同上 |
| OpenRouter Batch | 服务端幂等哈希派生运行；固定 batch_request 来源 | GET 核对任务、原连接、形态与已派发调用；不新建 POST | `test_provider_context_batch.py` |
| Chat 图片/PDF/音频/视频与独立媒体入口 | 明确 multimodal_request 来源；Vision 子调用单独继承 | 异步视频只在原来源/调用匹配时 GET；Realtime 清理不因缺身份被阻断 | `test_provider_context_multimodal.py`、`test_provider_context_video_recovery.py` |
| Chat、Workload、异步、Realtime 认证 | 本地管理员主体，purpose=certification；policy_revision=null | 认证身份绑定配置、模型、Adapter、Profile、契约和幂等哈希；确认/刷新不重新派发 | `test_provider_context_certification.py` |

桥接仍保留既有路由与统计语义；C1 没有完成 D8 的独立 Policy/证据归一化，也未把 AI Research 文献的 `gateway=default` 改成 scoped。D8 必须继续核对普通文献与 R5 样本隔离，不能从来源字段已经存在推断该隔离已经完成。

## 可选兼容分支的处理

- 删除 `verify_context` 在持久来源和传入上下文均为空时返回成功的分支。
- 删除 `workload_context` 对部分入口返回空来源的迁移分支。全部 37 个 Workload 都要求持久来源；注册表与来源集合有一致性测试。
- `ProviderWorkloadPreparedCall.call_context` 改为必填，prepare 读取必须存在的持久上下文，不再使用 optional reader。
- 仓储 claim 和部分内部 start 方法仍保留可选参数以读取/构造历史记录及明确返回缺来源错误；这不授予派发权限。仓储 dispatch 对缺来源失败关闭，旧记录查询不补造主体。
- Workflow 对明确未纳管 source 的 legacy 分支、Expert Team Planner 在宿主边界创建根来源，以及未启用 Managed 的本地/legacy 分支保留。它们不能把 `request=None` 自动标注为 Xpert，也不能绕过已启用入口的来源检查。
- 公开 Handoff 的内部来源字段被移除；桥接 DTO 拒绝客户端指定 tenant、actor、entry、connection 或 context。Provider 凭据、消息、媒体与模型正文不进入来源表。

## 静态登记变更依据

本次只更新已核对的指纹和委托点，不改变任何入口分类或将待迁移入口晋级。

| 登记变化 | 复审依据 |
| --- | --- |
| main.py 两个 `_run_workflow_response` 委托点 | 仅增加宿主固定的父来源及子任务引用；外部 Xpert/Handoff 原权限、编排和模型发送位置不变 |
| Shadow、Meta、Expert Team、Fusion、Route/Team Gateway | 固定 source 或绑定宿主执行；原 Transport 和调用预算不变 |
| Workflow、RAG 三类 Gateway、RAG Service、Skill Service、Sandbox Toolset | 父链、Job/评测来源及运行前检查；Sandbox 的 HTTP 候选仍是工具执行域，不成为模型发送入口 |
| Batch、Multimodal、Workload 认证、Realtime、Video、Vision | 派发/恢复前增加来源复核；保留各协议；异步恢复不提交新模型请求 |
| SpeechWorkspace、audio_catalog、STT、TTS 的基线漂移 | `171af984..HEAD` 的 ElevenLabs Profile、voice/speed/字符计数变化；发送仍走原服务端 Adapter。登记不等于模型认证 |
| 两个 Settings 组件的基线漂移 | B3 资格状态展示，无新增 Provider 发送或凭据路径 |
| models.ts 的基线漂移 | 目录快照、数据类型与价格窗口映射；唯一网络关键字命中为 Fusion 说明中的 `web fetch`，不是执行调用。本批不修改模型数据或统计 |

完整门禁结果和未完成项见 `docs/task-cards/provider-control-r9-c1.md` 的本次收口记录。旧测试结果不能代替严格来源检查后的回归，上游集成后必须重新核对指纹和交叉路径。
