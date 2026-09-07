# RPG-03 验收记录（待人工验收）

本轮实现及自动验收完成，implemented_pending_manual_acceptance、claimAllowed=false。模块 230 项、父仓 53 项通过；真实派发 5/5。最终证据见 RPG03_REAL_ACCEPTANCE.json，下文保留历史检查点。

## 基线与范围

固定基线 `80221379cec850a2b25f5eeeb410233062f3e1ea`，分支 `codex/ai-rpg-rpg03-runtime`。仅两个 AI RPG 目录及 server/main.py、server/tests/test_provider_chat_stable_chat.py、docs/MODEL_PROVIDER_CONTROL_PLANE.md 三个父仓例外。

P0 核对 PR #361 合并基线与已验收 RPG-02 两目录一致，原研究 MANIFEST 及 13 项登记匹配。67 项旧模块/探针文件继续按基线字节冻结。恢复检查使用 `git -c core.quotepath=false status --short`，主检出区仍为原 78 项状态；UTF-8、规范 LF（含尾换行）SHA-256 为 `EC8B7DEE86751C3F268BD42F64E87F9FF2EFF2F9D6468378E3A5839B65E8ACF6`。本任务未修改主检出区。

## 已通过的子批

| 子批 | 文件数 | 实际门禁与结论 |
| --- | --- | --- |
| P0 | 4 | 基线、研究/探针 hash、主检出区状态通过 |
| 03A1 | 5 | 新边界测试 10/10；冻结集 67；精确父仓例外通过 |
| 03A2 | 5 | Node 24.18.0/npm 11.16.0；npm 生成 lock；11 个包的版本、完整性及许可证不变；离线 ci 禁用安装脚本 |
| 03B | 3 | stable Chat、stable service、canary Chat 共 53/53（新增约束案例 20 项）通过 |
| 03C | 5 | 新合同 19 + 原合同 28 + 新边界 10，共 57/57；主审独立复跑新合同 19/19 与完整冻结边界 |
| 03D1 | 5 | 存储/恢复 13 + 运行合同 19，共 32/32；主审独立复跑存储/恢复 13/13 |

各子批的 `git diff --check` 通过。03A1 先用明确的 bootstrap 0.2.0 检查，03A2 起使用完整 0.3.0 边界。未改 RPG-01/02 历史聚合脚本常量。

可复跑的当前命令（模块目录）：

```powershell
node --test tests/runtime-contracts.test.mjs tests/contracts.test.mjs tests/rpg03-boundary.test.mjs
node --test tests/runtime-store.test.mjs tests/runtime-contracts.test.mjs
node scripts/check-boundary-rpg03.mjs
git diff --check
```

父仓验证命令：`python -m pytest server/tests/test_provider_chat_stable_chat.py server/tests/test_provider_chat_stable_service.py server/tests/test_provider_chat_canary_chat.py -q`。使用 Python 3.12.14 的现有只读环境，所有测试存储、cache 与 basetemp 指向本轮 `.rpg03-work` 的独立目录；未修改该 Python 环境。5 项已有 FastAPI 警告不影响 exit 0。恢复前的权限异常属于不同权限上下文读取旧 pytest 临时目录，并非最终测试失败。

## 证据与限制

03B 的 Provider 是可控假上游；03D1 使用真实本地文件和真实子进程退出。两者均不是实际 Provider 成功证据。03I 真实验收和 03J 总聚合尚待实施。

存储通过的边界、API 与 Windows 链接夹具限制见 `RPG03_PERSISTENCE.md`。测试失败后仅修复当批问题，未以其他通过项替代失败门禁。新测试目录保留在 `.rpg03-work`，不进入版本控制。

03E1 由 Sol 实现、Astra 复核并修复最后的 usage 帧次序边界，严格 5 文件；03E2 仅更新适配说明、验收记录和机器状态共 3 文件。HTTP/SSE 独立复跑 19/19 通过，67 个冻结文件边界与 diff 检查通过。预先取消、同 chunk 取消、永不返回回调、CRLF 跨片、未闭合 EOF、受控备用、标准 usage 尾帧、失败回执保留均有覆盖。全部来自 loopback 假服务；真实 Provider 调用和网站消息探针仍为 0。详见 `RPG03_HTTP_ADAPTER.md`。03F～03J 尚未完成。

03F 在同一调度目标下拆分为 03F1（核心、入口、测试、说明，4 文件）、03F2（取消恢复修复，5 文件）和 03F3（本记录与机器状态，2 文件）。独立 Sol 复核发现已接受取消但请求未结束时，原检查点未保存取消事实；已增加 `cancelRequestedRevision` 并在恢复回执保留 requested=true。中断恢复后的旧请求完成会被 CAS 拒绝，不能覆盖正式回合或状态。Astra 独立执行 `npm.cmd run test:runtime`：47/47（合同 19、存储 13、调度 15）；边界测试 10/10、冻结 67 和 `git diff --check` 通过。

03F 覆盖原代表内容、完整五天赋、查询、建议未选择、显式状态提交、丢弃、重复生成不重复派发、跨会话单请求、取消竞态与存储失败。测试为内存假适配器与本地恢复证据；未调用 Provider。复核另提出断电时目录元数据持久化的更强保证，现有存储说明已明确排除断电、内核崩溃和网络文件系统；本轮验证普通进程退出后的完整提交恢复，不扩大耐久性承诺。

03G1 为宿主、宿主测试和说明共 3 文件；03G2 为合同、核心、入口及核心测试共 4 文件；03G3 更新 5 个说明/状态文件。Astra 独立复跑插件宿主 9/9，以及 `npm.cmd run test:runtime` 53/53（合同 19、存储 13、核心 21）；冻结 67 和 diff 检查通过。所有插件均为直接注入的受信测试适配器，无模型、网络或外部代码加载。

03G 独立复核修复了 required 插件恢复时先读授权的死锁、授权历史最新记录歧义，以及 disable 失败后 read 意外清除 fault 的问题。只读快照不解除故障，显式 resume 才重新核对并推进 revision。旧 grant 重新 enable 后对同一资源的撤销后会话仍不能 invoke；旧结果也不能通过 validateResult。曾失败的恢复测试使用了恢复前 revision，已按实际返回 revision 修正并复跑整个运行门禁；没有改变恢复递增语义。

03H1 为 Python 验证工具、HTTP 集成测试、CLI 驱动/入口/测试共 5 文件；03H2 为验证说明、脱敏回执、模块 README、状态及本记录共 5 文件。Astra 在真实本地模镜 HTTP 和 loopback 假上游上完成两次回复与提交、查询、重复请求不派发、关闭/重开恢复和取消；CLI 作为真实子进程另完成 create/generate/commit/read/退出/resume。服务端 4 个 run 与 4 个已派发 attempt 对应 3 成功、1 client_cancelled，假上游实际观察取消连接断开。本轮真实 Provider 消耗仍为 0。

最终逻辑证据 `h1-http-cy7w_n53`：`RPG03_OFFLINE_HTTP_OK`；CLI 5/5，HTTP 集成 1/1，早期失败回执/非 main 源码漂移/本脚本进程终止升级的自检 3/3；所有本工具进程已退出。候选完整 server 树 SHA-256 `27c0f09da409e59d77f9325c6a6916112d8e276e18a733ef0dc0248f05a4b974`，模块实际执行源码 SHA-256 `b577b6496b39b73d9895d6c7a9fa50e632ed7b8275a440ee0a0394f76fb940b8`。详见 `RPG03_OFFLINE_HTTP_RECEIPT.json`；23 项模块源 hash 与执行前后检查一致。独立复核指出的早期失败证据、完整源码终点复核及 CLI 超时清理均已修复并验证。

额外按用户要求执行旧合同、内容、归档和世界/worker 扩展回归，共 128/128、exit 0。所有临时数据使用新的本轮忽略目录，不改变旧测试、资源或历史聚合脚本。03H 完整冻结边界与 `git diff --check` 通过；未将这些模拟证据作为 03I 的替代。

非必要的 maxTokens 32768→128000 扩展曾被自动审批拒绝，原因是扩大资源消耗边界并与现有文档不一致。已采用更小范围方案：保留 32768 数据合同上限；本轮真实验收仍限制每次 512，没有绕过拒绝或消耗额度。

本轮 Provider 消耗 0/4；网站消息探针 0，原探针账本 SHA-256 仍为 `79929F95E4D91053C85542947CBF9084ED0BB286C17B04FE3CA105CBB787A99E`。只读查看共享实例公开模型目录未读取凭据、复制数据库或派发 Provider 请求。独立候选实例的精确模型、连接和资格仍需在 03I 前确认。

## 03I 接续准备与未完成门禁

已在忽略目录 `.rpg03-work/i-real-candidate-q61xxnln` 准备独立候选代码、空 `.env`、隔离存储配置及 `start-candidate.ps1`。仅生成本候选的本地管理配对密钥；没有读取或复制供应商凭据、共享数据库。脚本未执行，`127.0.0.1:18303` 不是已启动的验收实例。公开元数据见 `RPG03_REAL_PREFLIGHT.json`，私有配置和本地管理配对密钥不得进入版本控制或公开输出。

接续时先确定供应商及精确轻量模型 ID，复核候选源码、启动脚本、基线和两个账本 hash，再确认端口空闲后启动本候选。通过模镜既有管理会话、连接、目录及认证流程配置独立 Provider 存储；密钥只在该配置流程录入，不放入 Node 配置、卡包或聊天文档。配置前不派发模型。必要认证也要先在调用账本预留并计入最多 1 次认证额度，不能用预写资格、共享数据库副本或假上游资格替代真实认证。

真实验收保留 2 次短回复和 1 次流式取消尝试，每次最多 512 output tokens，无自动重试；失败、认证失败、未知派发及取消竞态都计数。正常回复需核对服务端回执、运行回执与账本，第二次显式传递第一次已提交回复；取消晚于完成则如实记录竞态。确定性取消已有 03H 模拟证据，但不替代该真实尝试。

本次 03I 文档子批只修改 5 个版本控制文件：本记录、机器状态、公开预检、研究计划和研究 MANIFEST。准备文件存在不代表 03I 通过。当前缺少精确模型、独立受控连接和资格证据，状态为 `awaiting_real_configuration`、`claimAllowed=false`。03J1/03J2 尚未进入；`verify:rpg03` 仅有 package 脚本声明，其目标 `scripts/verify-rpg03.mjs` 尚待 03J1 实现，不能运行该声明来宣称总门禁已通过。未设置 `implemented_pending_manual_acceptance`。

接续复核通过新边界 10/10、完整冻结检查 67 项、研究清单 14 项及执行源码 23 项 hash；私有候选 1277 项 server 文件同时与当前 worktree、候选副本和完整清单 hash 匹配，启动脚本与原 HTTP 回执也匹配。只读监听检查确认 18303 无监听。恢复检查曾使用 Git 默认中文路径转义，导致状态摘要不同；按上述固定采集格式重跑后与原 hash 完全一致，未更新或放宽基线。两个账本保持原字节，真实调用为 0/4。

未 Commit、Push、PR、Merge、Deploy、Release 或 Publish；未进入 RPG-04。回退只停用本轮隔离模块/实例并保留证据，不触碰共享服务和存储。

## 03I 独立控制链配置（历史检查点）

用户选择官方 OpenAI Luna，授权脱敏读取指定 key；随后允许 OpenRouter 备用，并确认使用模镜控制面链路、两把 key 均可配置。官方模型 ID 和快照为 `gpt-5.6-luna`，支持 Chat Completions 和流式输出；支持 `reasoning_effort=none`。参考 [官方模型文档](https://developers.openai.com/api/docs/models/gpt-5.6-luna)。这不是账户可用性证明，仍待真实认证。

核查发现当前 `server/model_router/chat_control.py` 强制文本首路由 kind=newapi；`server/tests/test_provider_chat_control_service.py` 的首路由拒绝与合格 newAPI/备用两项测试独立复跑 2/2。该规则不因官方或 OpenRouter key 有效而解除，因此没有把官方连接冒标为 newAPI、修改资格/路由规则或退到 legacy。按用户最新确认，复用本机已有固定 newAPI 镜像，建立独立 `127.0.0.1:18304` 网关，模镜候选继续使用 `18303`。

镜像为 `v1.0.0-rc.22`、digest `sha256:d600f20c2781e1a173c2a02f8c33b0c4b1b4e8e5a8b107bafaf2442ae2c9386c`，对应源提交 `bc14c18f6024e79cba1c08d02cd007796e12d668`、AGPL-3.0。它是验证环境中的既有网关镜像，不向 RPG 包复制源码或增加 npm 依赖。固定源码已核对初始化、管理会话、渠道、Token 和重试选项接口；其 GPT-5 适配保持原值转换 `max_tokens`，无需扩大父仓允许范围。[固定网关源码](https://github.com/QuantumNous/new-api/tree/bc14c18f6024e79cba1c08d02cd007796e12d668)

已通过正常管理 API 完成独立初始化、RetryTimes=0、官方 Luna 渠道（type=1、reasoning_effort=none）、手工停用的 OpenRouter Luna 渠道（type=20、status=2）、仅允许官方 Luna 的有限内部额度 relay Token，以及模镜 kind=newapi/chat scope 连接。两级本地模型目录均包含精确模型；没有调用渠道 test、balance、上游 fetch_models 或认证接口。newAPI 内置倍率只用于它自己的内部计量，不作为供应商真实报价。

`RPG03_CONTROL_CONFIGURATION_OK` 的公开来源、配置/启动 hash、镜像许可证及连接引用固化在 `RPG03_REAL_PREFLIGHT.json`。生命周期只持有本轮创建的容器/进程，最长 40 分钟后停止并保留存储；也可用同一私有目录的 controlled-stop 标记主动停止。启动脚本首次因 Windows 权限上下文无法读取而未启动；将相同无密钥源码保存到同一执行上下文后通过。首次配置只在根 .env 查找 OpenRouter key 未找到，未建渠道、未派发；后经脱敏定位到主仓 server/.env 的唯一匹配项，正常接续配置。

这一配置子批仍只修改 5 个文档/状态文件。两把供应商 key 仅由本地进程读取并通过独立 newAPI 管理接口保存；模镜只保存新生成 relay Token。公开输出没有 key，卡包、Node 配置和版本控制无 key，共享实例和共享数据库不变。真实调用仍为 0/4、网站探针为 0。接下来须先登记唯一一次认证，再根据实际结果推进；03J 尚未开始。

## 03I 真实认证与首个回复失败（最新检查点）

官方 `gpt-5.6-luna` 通过正常模镜认证接口完成真实认证：10 input + 4 output tokens，精确模型一致，终态和文本流检查全部通过。随后正常设置 `newapi_preferred`、精确稳定模型及唯一受控连接，公开控制状态为 qualified。为使用网关已有标准化输出，认证后设置官方渠道 `setting.force_format=true`，保留 reasoning_effort=none、RetryTimes=0、备用停用；历史认证和新输出配置分别留有回执，不追改认证证据。

首个正式生成已派发，官方渠道消费日志为 70 input + 43 output tokens。运行适配器收到 148 字符草稿后报 `RUNTIME_ADAPTER_EVENT_INVALID`，未取得终态服务回执，因此会话 revision=2、failed generation、formalTurns=0、pending=null、无状态写入。原五天赋玩家和零可选插件资源绑定有效。未保存原始 SSE wire，尚不能把某个具体尾帧认定为已观察根因。

模镜管理接口仅有一个 normal run/已派发 attempt，终态为 client_cancelled、total_tokens=113；网关仅有认证和此次回复两条消费记录。该客户端断开来自协议失败，不能计作计划中的取消验收。运行用量仍为未知，另列网关报告用量，实际供应商费用保持 null。详见 `RPG03_REAL_PROVIDER_RECEIPT.json` 和 `RPG03_CALL_LEDGER.json`。

当前累计 2/4，剩余 2。失败后未重试、未启用备用、未派发第二回复或取消。两个本轮自有实例均已停止，生命周期 exit 0，服务子进程被正常收尾终止后 exit 1；容器、数据和证据保留，共享服务未动。03I 失败门禁保持开放，03J 未进入。

本调用证据子批仅修改调用账本、机器状态、预检、真实回执及本记录 5 个版本控制文件。后续先离线定位/修复并复核当批门禁；要完整取得两次连续提交回复和一次真实取消仍需 3 次派发，超过剩余 2 次，需额外授权 1 次后才能恢复真实验收。不能将认证成功或已有模拟通过提升为本轮验收完成。

## 03I 离线修复后停批交接（历史）

根据 pinned newAPI Usage DTO 构造的测试，修复前可稳定复现尾帧拒绝。Sol 只修改 runtime/node/http.mjs 和 tests/runtime-adapter.test.mjs；独立复核再修复未知 details 子字段未拒绝的问题，Astra 保留原 prompt/completion details=null 兼容。三主 token 仍是唯一权威，扩展不赋予状态、权限或费用语义；未列明的 billing/cost/semantic 等可选字段仍拒绝。原始实际 SSE 未留存，结论限于源码推导复现。

修复子批加上 HTTP 说明、机器状态、RPG03_I_REPAIR_RECEIPT.json，共恰好 5 个版本控制文件。Sol 的红绿证据有逻辑路径和 hash；运行及插件回归 62/62。Astra 对最终字节独立复跑 adapter 21/21、边界 10/10、冻结 67、git diff --check，并在 h1-http-zk20vnwt 完成实际本地模镜 HTTP＋假上游的创建、两次提交、恢复、取消及 CLI 链。RPG03_OFFLINE_HTTP_OK，4 次均为假上游，本工具进程已停止；最终模块源 hash 为 fde3fd7489030aea4f4ae77cf338fed759369a3b9cb38aef437b270dc2ca58a1。

旧 h1-http-cy7w_n53 和真实失败使用的模块 hash b577b649… 保持历史原值；新验证不覆盖它们。主检出区仍为 78 项既有变动，其固定格式状态 SHA-256 与接管值一致。真实实例容器已停、两个原自有 PID 已退出、18303/18304 无遗留监听；私有凭据、数据库和会话不进版本控制。

停批文档子批仅更新本记录、研究 README、RPG03_PLAN 和 MANIFEST 四个文件，不进入 03J。当前累计仍为 2/4，后续真实复测未开始。完整接续至少需额外 1 次派发授权，并复查既有 24 小时认证资格；若资格已失效，不擅自再次认证。恢复须保留 failed generation 和旧一次性派发标记，以新 generation ID 和有界私有 SSE 证据完成真实验收。仍未 Commit、Push、PR 或发布，RPG-04 未授权。

## 03I 完成与 03J1 聚合（最新）

用户明确追加 1 次额度，总上限从 4 调整为 5。原认证仍有效，经正常公开控制状态和管理 API 复核后，恢复同一个自有网关和模镜隔离存储；未重新认证、未改共享服务、未重置失败计数。新的 session.real.resume1 和 generation.resume1.* 保留旧 failed session；新增驱动在私有目录有界保留 SSE。

两次真实正常回复分别报告 70/43/113 和 169/47/216 input/output/total tokens，均有合格终态回执、精确模型观察、候选校验及明确提交。第二次恢复同会话并显式传入第一个已提交回复，查询无状态提交。第三次收到草稿后请求取消，requested=true、clientAborted=true、upstreamConfirmed=null；两个正式回合和状态不变。再离线恢复后 revision=12，formalTurns=2、pending=null，取消记录完整，无模型重放。

本轮总计 5/5：认证 1、历史失败回复 1、正常成功 2、取消 1。管理接口共 4 个运行及 4 个已派发 attempt，网关共 5 条消费记录，均为官方 Luna 渠道，无自动重试或备用派发。正常回复的三主用量一致。取消的网关计量为 60 input + 2 output，但运行/服务端未取得完整用量，保持未知；费用仍 null。SSE request_id 不可用，对账采用独占串行时窗、精确模型、唯一 attempt、顺序与正常用量，未补造跨系统请求 ID。权威记录见 RPG03_REAL_ACCEPTANCE.json，历史失败回执原样保留。

第一次取消启动前曾因自动审批的 Codex 用量限制整体拒绝，没有写取消标记或消耗第五次派发；用户恢复后先核对账本仍 4/5、无取消标记，再执行唯一取消请求。真实模型额度和 Codex 工具用量分开。两个自有实例最后均停止，保留容器、数据和私有回执；服务终止 exit 1、生命周期正常 exit 0，不解释成模型失败。

03I 接续证据子批为账本、真实验收回执、状态、预检共 4 文件。03J1 新增 verify-rpg03.mjs、4 项验证器正反例及父仓回归回执，另更新状态和本记录，共 5 文件。Sol 因账户用量限制未参与最终聚合实现；该部分由 Astra 实现并实测，不宣称经过独立 Sol 复核。

`npm.cmd run verify:rpg03 -- --base 80221379cec850a2b25f5eeeb410233062f3e1ea` 的底层同参数 Node 命令已返回 RPG03_AUTOMATED_GATES_OK：模块 230 项（原业务 128、运行 53、HTTP 21、插件 9、CLI 5、边界 10、验收器 4），冻结 67，父仓回归回执 53。父仓三套测试已在新 j1-parent-oh2v7xol 候选副本中实际复跑，53 passed、exit 0，来源树 hash 与真实验收一致。聚合只读取真实回执，不自动认证或调用模型；mock 分类、源码漂移、超额/重复账本、缺回执、未提交连续性和伪取消负例均拒绝。原 RPG-01/02 聚合脚本与常量保持冻结。

03J2 已同步研究入口、路线、审计、计划及 MANIFEST，14 项 hash 匹配。最终状态为 implemented_pending_manual_acceptance、claimAllowed=false。RPG-04 只消费准备消息接口、生成/会话/回执；RPG-05 消费流事件、候选、commit/discard；RPG-06 做整体竖切。本轮未实现主持提示词、检索、玩家 UI、长期记忆、市场、外部插件加载或全量提取，卡片特化规则没有进入核心。
