# RPG-03：独立运行框架与模镜受控接入

状态：本轮已实现，真实及自动门禁通过，待用户人工验收，claimAllowed=false。日期：2026-09-06。原失败记录和授权演进保留；完成证据见 RPG03_STATUS / RPG03_ACCEPTANCE，不以计划代替通过证据。

## 固定基线、路径与授权

基线 80221379cec850a2b25f5eeeb410233062f3e1ea（PR #361 合并）；已验收内容提交 82484ef568c784dfc6c0b03480b2049a19c18df5。两 AI RPG 目录一致。独立 worktree C:\tmp\modelmirror-ai-rpg-rpg03，分支 codex/ai-rpg-rpg03-runtime。
只允许 experiments/ai-rpg-engine/**、docs/ai-rpg-experiment/**，及父仓三个精确例外：server/main.py、server/tests/test_provider_chat_stable_chat.py、docs/MODEL_PROVIDER_CONTROL_PLANE.md。主检出区、共享服务和存储不改；不 Commit/Push/PR/Merge/Deploy/Release/Publish，不进入 RPG-04。

零可选插件运行；不实现提示词/世界书检索、长期记忆、玩家 UI、市场、外部插件代码加载或全量提取。任务经济、死亡重生、结算继承留在卡片内容。全量提取仍在 RPG-06 完成后另行授权。

## 交付接口与不变量

私有包 0.3.0，Node 24.18.0 / npm 11.16.0；不增 npm 依赖。新增 /runtime、/runtime/node，冻结原根导出、/content、四种 0.1.0 合同及已有内容工具。核心通过端口执行；文件、网络、进程仅适配/验证层。

新增可校验的会话、生成事件/回执、提交和插件授权记录，绑定 session、card/player hash、generation/exchange、revision、真实/模拟证据。诊断不得回显正文、凭据、绝对路径或堆栈。
运行提供创建/读取/恢复、generateTurn、cancelGeneration、commitTurn、discardTurn。只接收已准备消息，不组装/检索/裁剪/总结。单实例一请求；pending 未处理不能继续生成。相同生成 ID 和内容不重复派发，异内容冲突。
流片段是草稿，完整原合同通过后才 pending。提交需 current revision + pending exchange ID；正式回合和选中声明状态同次原子落盘。query 无状态、suggestions 不执行；不解释 extensions。取消、失败、恢复、提交更新 revision。
普通 /api/chat 不存历史，模块最小文件检查点保存资源绑定、正式回合、声明状态、生成状态及回执。独占锁/串行写/原子替换。重启 in-flight 标 interrupted、不重放；损坏、hash/版本不符、不能确认失效的锁均拒绝覆盖。正文只在私有会话区；不保证崩溃前最后流片段持久化，保证最后完整提交可恢复。

插件宿主默认空：manifest/受信绑定版本和 hash、依赖、显式授权、数据范围、启停与故障结果隔离。必需不可用阻断；推荐 core/omit/readOnly 降级。只注入受信测试 hook；异常/超时/停用后的迟到结果不改核心。未接入服务不可用；权限不产生模型/记忆/网络访问。不加载外部代码，不称进程内异常处理为安全沙箱。

## 模镜适配与额度

ChatRequest 新增 require_managed_route: bool=False。true 仅允许 default + 纯文本，拒绝 routing/tool/Skill/media/output 旁路；现有 Managed preflight 未接管时在 Provider 派发前稳定拒绝 provider_chat_managed_route_required。复用 preferred/required、资格、派发前复核与回执；不退 legacy，不追加重试，不改全局晋级门槛。缺省/false 兼容。

Node adapter 仅连可信配置的模镜地址，不读取供应商 key/卡片 URL、不重定向。先确认 OpenAPI 支持；逐次核对 exact model/status；恒传 require=true、tool/output none、compression off。严格 UTF-8/SSE/终态/回执；缺失或模型不符不形成正式回合，未知值不补造。取消区分请求、客户端中止和上游确认，不承诺撤销费用。

独立候选 Python 3.12 后端/requirements、Provider Store/运行目录，默认 127.0.0.1:18303。不更新共享 :8000，不复制共享数据库/凭据。真实批次前固定候选代码、服务、已认证轻量模型 exact ID、连接引用、目录/资格和账本；由既有模镜配置流程提供凭据，缺合格配置停止真实批次。
最多 4 次 Provider 派发：最多 1 次必要认证、2 次正常短回复、1 次流式取消。每次输出 <=512 tokens；失败/认证失败/取消竞态计数，未知派发按消耗，零自动重试。真实取消晚于完成如实记竞态；确定性取消由可控假上游门禁证明。测试只用中性短文本，不发完整玩家或站点资源。
网站消息探针为 0；原 20 次账本不借用、不重置。编码子智能体不计 Provider 实验额度。

## 子批与门禁

每语义子批 <=5 个版本控制文件；失败停当前批，可继续拆分但不削弱目标。
| 子批 | 交付 | 门禁 |
|---|---|---|
| P0 | worktree/计划/基线 | 固定提交、两目录、原13项研究 hash/账本、主检出区不变 |
| 03A1 | 边界/冻结清单 | 精确父仓例外、分层、冻结旧合同 |
| 03A2 | package/lock/登记 | 无新增依赖，完整性/许可证 |
| 03B | 服务端约束 | flag/legacy/名单/竞态无旧路径派发，缺省兼容 |
| 03C | 运行合同 | 引用/revision/pending 绑定/稳定诊断/不变输入 |
| 03D | 存储 | 原子性、单写、损坏拒绝、恢复不重放 |
| 03E | HTTP/SSE | 旧服务拒绝、受控、异常/取消/回执 |
| 03F | 调度/提交 | 去重/query/旧候选/无半回合 |
| 03G | 宿主 | 零插件、必要阻断、推荐降级、权限/故障隔离 |
| 03H | CLI/离线贯通 | 真实模镜 HTTP + 假上游创建/生成/提交/恢复/取消 |
| 03I | 有界真实调用 | 配置、次数、两次连续回复、回执/取消分类 |
| 03J1 | 模块验收/状态 | 必需门禁，mock/real 分栏 |
| 03J2 | 研究同步/MANIFEST | 文档/实现/hash/后继边界一致 |

Astra 管架构、接入异常、浏览器串行、调用额度和最终验收；Sol 只做明确离线子批。不开 Luna / 全量 worker。
新增 test:boundary:rpg03、test:runtime、test:adapter、test:plugin-host、verify:rpg03。原 28+43+18+39=128 业务测试保持；历史轮次聚合/边界原样，不改旧常量制造通过。父仓 stable Chat/service/canary 回归及请求约束/竞态。原卡/五天赋玩家、query/suggestions、非法状态、取消恢复、插件降级覆盖。范围/hash/MANIFEST/git diff --check 通过。

完成后状态 implemented_pending_manual_acceptance / claimAllowed=false。RPG-04 消费已准备上下文/生成/会话/回执；RPG-05 消费流/候选/提交放弃；RPG-06 竖切。不推断 UI、记忆、提示词、全量已实现。回退停用隔离实例/模块并保留证据。

## 历史停批检查点（追加授权前）

截至本次接续，P0～03H2 的局部门禁已通过：旧业务 128 项、运行合同/存储/调度 53 项、插件宿主 9 项、CLI 5 项、真实本地模镜 HTTP＋假上游 1 项、验证工具失败路径 3 项及父仓 stable Chat/service/canary 53 项。03I 兼容修复将 HTTP/SSE 测试从 19 项增加至 21 项，原测试保留。冻结旧文件 67 项；所有假上游回执明确分类为 mock，不证明真实 Provider 成功。

03I 的独立候选代码与启动配置存于模块忽略目录，逻辑引用为 i-real-candidate-q61xxnln。用户已选择官方 gpt-5.6-luna，授权脱敏读取指定 key、配置 OpenRouter 备用，并确认使用模镜控制面链路。因现行文本首路由强制 newAPI，使用本机已有固定 v1.0.0-rc.22 镜像建立独立 newAPI:18304，连接独立模镜:18303；未修改共享实例、控制面政策或父仓允许范围。两级目录、正常管理配置和关闭自动重试均通过，OpenRouter 渠道停用备用。真实认证通过，随后首个运行回复未通过流协议门禁；现两个实例均已停止并保留证据。

公开预检为 experiments/ai-rpg-engine/docs/RPG03_REAL_PREFLIGHT.json，记录镜像 digest/AGPL-3.0/固定源码、候选和配置 hash。网关仅用于独立验收，不复制源码进 RPG 包、不增加 npm 依赖。供应商 key 仅通过独立网关管理 API 配置；模镜只保存新 relay Token，卡包、Node 配置、公开文档或聊天不含密钥。此配置调整来自用户本次对控制面链路和两把 key 的明确确认。

真实调用账本当前为 2/4：认证 1 次（10 input + 4 output），失败回复 1 次（网关报告 70 input + 43 output）。运行适配器报 RUNTIME_ADAPTER_EVENT_INVALID，没有候选、正式回合或状态写入；模镜因客户端断连记录 client_cancelled，不能把它计为取消验收。两条消费记录、唯一 normal run/attempt 与账本匹配。费用和未取得的运行用量仍保持未知。

根据固定网关 Usage DTO 离线构造尾帧，可复现旧适配器对扩展字段的拒绝。已只在 HTTP 适配器与测试中加入明确字段/类型白名单，保留三主用量与回执核对及原 nullable details。原始失败 SSE 未留存，不能把源码推断写成精确致错帧已实测。该修复不宣称支持网关全部可选 billing/cost/semantic 字段，也不把网关扩展转成经济或状态语义。

最终离线修复回执为 experiments/ai-rpg-engine/docs/RPG03_I_REPAIR_RECEIPT.json。Astra 复跑 adapter 21/21、boundary 10/10、冻结 67，并在新目录 h1-http-zk20vnwt 完成实际本地模镜 HTTP＋假上游贯通，最终模块源码 hash 为 fde3fd7489030aea4f4ae77cf338fed759369a3b9cb38aef437b270dc2ca58a1。原失败及旧离线源码 hash 保持历史原值，未被新通过记录覆盖。

剩余真实验收需要两次连续提交回复和一次取消，共 3 次；现仅余 2 次，因此须额外授权 1 次，才能恢复这一完整验收批。认证默认有效期为 24 小时，恢复时还须正常复查目录/连接/资格；若已失效，另行处理认证授权，不补造资格或重置账本。失败会话、一次性标记和旧驱动不能覆盖重跑，新复测用新 generation ID，验证工具在私有目录有界保留 SSE 证据。

网站消息探针仍为 0，原 PROBE_LEDGER 字节未变。03I 尚未通过，不能进入 03J1/03J2；verify:rpg03 的聚合实现及最终路线/审计收口仍未完成。机器状态为 awaiting_additional_dispatch_authorization / claimAllowed=false。本次仅同步停批恢复入口和回执索引，不算完成 03J2，不构成提交、发布或 RPG-04 授权。

## 最终交付与授权演进

用户批准追加 1 次派发，总上限由 4 改为 5，认证配额仍为 1，失败计数不抹除。保留原资格，经正常只读复查后完成两次连续提交回复和一次真实取消；总计认证 1、历史失败 1、正常成功 2、取消 1，5/5 已消耗，无重试或备用调用。最终真实回执为 experiments/ai-rpg-engine/docs/RPG03_REAL_ACCEPTANCE.json。取消客户端已中止，上游确认和实际费用未知。两个真实验收实例已停，私有存储/失败证据保留。

独立运行与控制面接入、最小文件连续性、受信插件接口和 CLI 完成。聚合命令 `npm.cmd run verify:rpg03 -- --base 80221379cec850a2b25f5eeeb410233062f3e1ea` 检查 230 项模块测试、67 冻结文件、依赖及许可证、研究 hash、当前源码绑定、真实证据和父仓 53 项回归回执。旧 128 项业务测试与历史聚合脚本未改；真实本地 HTTP＋假上游、真实 Provider 分栏保存。四项新增验收器反例覆盖 mock 冒真、漂移、账本错误和伪取消。

后续消费接口与排除项不变。RPG-04 接准备消息、生成、会话和回执；RPG-05 接流、候选及提交/放弃；RPG-06 做整体竖切。不从这轮推断 UI、检索、提示词、记忆或全量已实现。当前无 Commit/Push/PR/Merge/Deploy/Release/Publish 授权，人工验收前不进入下一轮。回退停用实验模块和自有实例，保留证据，不触碰共享服务与存储。
