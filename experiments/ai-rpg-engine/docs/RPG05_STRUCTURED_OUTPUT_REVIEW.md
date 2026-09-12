# RPG05 受控结构化输出接入评审

状态：2026-09-12，已获明确范围授权并完成离线/mock实现；尚未真实验证。原2026-09-11评审及调用限制保留。保持原提示词、回合合同、历史响应及账本不变。

## 结论与证据

官方 gpt-5.6-luna 页面明确标记 Streaming 和 Structured outputs 为 Supported。Chat Completions 可通过 response_format 的 json_schema 与 strict:true 请求严格结构；这不等于本地 newAPI 镜像、渠道配置及完整模镜链路已验证。

来源（已打开核验）：
- https://developers.openai.com/api/docs/models/gpt-5.6-luna
- https://developers.openai.com/api/docs/guides/structured-outputs
- Context7 /websites/developers_openai_api，Chat Completions strict schema 文档。

本地真实路径已核对：runtime/node/http.mjs 构造请求未带 response_format；旧独立 candidate-code/server/main.py:3089 的 ChatRequest 无该字段；普通上游参数构造也没有该字段。provider_chat.py 的 build_authorized_stream_request 可以携带 payload，但不能据此推断入口支持。原聊天资格只证明文本链路，不证明严格Schema生效。

## 推荐设计

保留 Chat Completions、官方 Luna、原提示词、原响应JSON和最终 validateTurnExchange 校验。增加宿主专用的结构化输出配置，浏览器无权提交任意Schema、系统消息或模型地址。配置通过冻结清单绑定卡包hash、Schema编译器版本、编译结果hash、模型、路由和参数。

生成一份“该卡包与该回合专用”的生成Schema，不直接上传旧通用TURN_EXCHANGE_SCHEMA：
- 根仍为原六字段对象，所有对象additionalProperties:false。
- moduleRef 与对应values使用嵌套anyOf的明确分支；fieldRef与其value类型同分支绑定。不能把所有ID的enum与所有值类型union独立拼接，那仍允许错配。
- stateProposals按卡包声明的可提议字段类型生成分支；查询回合禁止非空状态提案。状态shortText与展示list保持分离。
- 既有可选字段不能直接改成nullable，否则旧合同可能拒绝null。用“缺少可选字段/包含该字段”的互斥对象分支表达，或经审批采用更窄的合法输出子集并明确收窄。不得静默丢字段或强制所有模块出现。
- oneOf不能盲目改成anyOf；先证明分支互斥（类型或固定鉴别值）。不支持关键字须逐项登记等价展开或保留本地约束，不能删掉后声称语义等价。
- Schema检查尺寸、深度、枚举规模和递归引用；生成结果稳定、可复现。卡包引用、重复项、跨字段规则、状态权限等继续由旧验证器作最终裁决。

严格输出仍需要处理refusal、length、content_filter、取消和断流。它减少格式错误风险，不保证剧情质量、事实正确或运行成功。任何非正常完成均不得自动提交、重试或降级为普通文本。

## 精确实施边界（2026-09-12已批准）

1. RPG05目录内新增纯Schema编译器、离线兼容检查、测试；不修改src/index.mjs、runtime/contracts.mjs、context/compiler.mjs或RPG04提示词。
2. RPG05新增受控HTTP适配器或获批的适配扩展；保留原适配器字节及默认行为。记录客户端收到的终止原因、DONE与原始流hash/有界私有流证据，标明它不等于供应商最内层原流。将Schema参数单独纳入请求证据与账本绑定，不能只记录旧request hash。
3. 必须有模镜服务端入口及上游参数传递。建议独立服务端变更批次，精确目标为server/main.py中的ChatRequest、build_upstream_payload与稳定聊天分支，以及新增server/tests/test_provider_chat_structured_output.py；测试期间不改共享部署。该精确父仓范围已获用户批准，当前已实现离线透传与测试。
4. 不就地改旧RPG04 candidate-code，不复制其凭据或数据库。不以新的直连代理绕过模镜治理。候选后端的基线、资格与配置接入须在上述服务端批次中明确。若必须扩展额外文件，先报告精确依赖后再批准，不放开整个server目录。

## 小批门禁

A：最多5文件，纯Schema编译器及测试。用两世界配置，证明固定ID、list/shortText、模块字段关联、空数组、可选字段、非法引用及重复约束；用原验证器交叉验证。只做离线。

B：最多5文件，服务端最小透传。假上游捕获确切请求，验证strict/schema/hash传递；不支持必须显式拒绝，禁止参数静默忽略、普通文本降级、自动重试。未启用时原聊天行为不变；非法来源/任意路径/超限Schema拒绝。原稳定聊天测试与新fake HTTP测试通过。

C：最多5文件，RPG05适配及终止证据。mock覆盖完整响应、拒绝、length、取消、断流、迟到数据、重复点击和错配Schema；响应原字节保持，正式历史不污染。

D：冻结源码、Schema、两世界资源、原提示词hash、路由资格与新预算后，实际UI测试。先证明newAPI确实接受并执行strict；普通文本认证不可替代。真实探针计入Provider账本。

## 预算和回退

当前仍7/11消耗，余4次；原剩余Gu查询+Minecraft三回合+取消共至少5次，结构化输出资格验证另需预算考虑。当前没有新增派发、重认证或重置授权。切换生成模式后的结果与旧两条成功回合为混合配置，必须分栏，不能声称六回合同一配置完整覆盖。

回退关闭新模式并撤回本批新增入口/模块，保留证据与会话。旧冻结已因前批诊断代码变动失效，不能直接恢复真实调用；必须重新冻结。未安装依赖、未启动服务、未新增模型调用、未Commit/Push/PR/Merge/Deploy/Publish。

## 2026-09-12 实施与复验

- A：新增 structured-schema.mjs 与测试；纯编译、严格输入校验、卡包字段和值类型关联、可选字段互斥展开、oneOf分支互斥证明、尺寸/深度/枚举预算。hash只依赖本地加密函数，没有读取资源或运行网络；原最终合同校验保留。
- B：精确修改 server/main.py，并新增 server/tests/test_provider_chat_structured_output.py。response_format 默认不传；仅 RPG05_STRUCTURED_OUTPUT_ENABLED=true、loopback、无浏览器来源且 require_managed_route=true 的文本请求允许透传。假上游验证原样传递及不支持时失败，不重试。该开关未在任何服务启用，代码未部署。
- C：新增 structured-http.mjs，派生自冻结 runtime/node/http.mjs，原文件不动。模型输出不修补；Schema错配、refusal、length、content_filter、断流、迟到事件和取消均失败。保存至多1MiB客户端收到的原始SSE字节、base64、SHA256、finish_reason、DONE与完整性；这是模镜客户端边界证据，不是供应商内层原流。正常输出仍需原运行时合同验证。
- 受控包装器将 responseFormat 与 request 一起纳入账本requestSha256，并在POST前保存私有请求。新模式的终止诊断引用实际流观察；旧模式结果保持原样。浏览器没有Schema提交入口。
- 新模式只能由重新批准的冻结记录 structuredOutput 指定：format=rpg05-strict-output/1、compilerVersion=rpg05-structured/1，绑定编译器/适配器/包装器/冻结器文件，以及本工作区 server/main.py 和新增服务端测试hash、结构化资格证据hash。当前没有这种执行冻结，不改旧freeze/账本。后续候选后端部署与资格实测仍未完成；工作区hash不等于运行进程hash。

验证：node scripts/verify-rpg05.mjs：417项（旧216 + RPG04非HTTP125 + 宿主74 + UI安全2），typecheck/build通过。回执 .rpg04-work/rpg05-verify-1789201667898/receipt.json。随后只调整新模式诊断回执字段，运行 ui-host-real-adapter + ui-host-execution-freeze，11项通过；417项聚合回执保留为该调整前快照，不伪改时间或hash。

服务端命令：C:\tmp\modelmirror-mcp-test-venv\Scripts\python.exe -B -m pytest server/tests/test_provider_chat_structured_output.py server/tests/test_provider_chat_stable_chat.py -q -p no:cacheprovider --basetemp=experiments/ai-rpg-engine/.rpg04-work/structured-server-20260912-resume2：57 passed，5条既有框架弃用/重复operation ID警告。包含实际JS编译Schema到Python请求合同的交叉验证及假上游HTTP测试。原始pytest输出保存在任务工具记录，机器汇总注明该来源，不冒充落盘原日志。

失败保留：早期GBK写入中断导致测试文件缺失；默认pytest临时目录权限导致26 passed/23 setup errors，换用本模块独立临时目录后同组51项通过，补强后57项通过。Windows换行曾造成整文件差异，已恢复两个已改文件原LF格式；server/main.py最终仅71行新增，git diff --check通过。

范围：mock/offline已验证；real新增0；manual本批未执行；independent本批未执行。独立旧HTTP harness保留先前fake-only证据，本批未重跑，不能据此证明新的真实链路。两世界沿用同一卡包的场景配置，Schema按卡包+本回合输入绑定，不声称本批完成两世界真实回合。

余项：部署独立新候选并冻结运行字节/资格、批准失败后的续跑及预算、完成实际UI剩余回合与取消、人工质量/体验验收、独立评审。现有4次剩余额度仍少于原剩余5次，结构化资格探针另计；本次审批没有扩额或解除slot7停止门禁。回退关闭新开关/撤回本轮新增适配与精确透传，保留全部会话和证据。没有Commit、Push、PR、Merge、Deploy或Publish。

## 续跑授权后的实际预检

用户明确批准总上限13：已用7，结构化资格1、Gu查询1、Minecraft3、取消1。新增 structured-ledger.mjs 保持旧7条hash与3份旧policy，只追加固定6槽；CLI新增 --structured。新旧账本与冻结测试19项通过。尚未创建新的执行冻结、policy或真实派发记录。

启动新独立候选（本RPG05基线的server源码快照），仅恢复指定隔离newAPI，服务内部沿用现有配置，无复制数据库或输出凭据。只读控制面查询显示 available=false；进一步确认 provider_chat_certification_expired。普通路由资格已过期，不能以结构化探针绕过受控入口。实际Provider仍7次。新候选和本次恢复的隔离实例已请求关闭，停机回执保存在 .rpg04-work/rpg05-structured-20260912/route-stopped-structured.json。

预算差异：完成原定结构化探针和5次UI验收之外，另需普通聊天重新认证1次（最多512），建议总上限14。该额外1次尚未授权，不能偷换为已批准结构化资格槽。当前停止，保留总13预算实现与现场证据；不修改TTL、不复用过期资格、不自动重试。未Commit/Push/PR/Merge/Deploy/Publish；只启动过获批的本地隔离验收候选，不涉及共享或生产部署。


## 2026-09-12 固定键后续修复（当前状态）

旧 strict Schema 的真实第12次调用仍在完整合同处失败：重复 info.rpg04.world。用户随后批准仅宿主固定键格式及显式转换，先离线验证。当前实现、444项聚合/59项父仓回归、失败保留和新格式待实测边界见 [RPG05_KEYED_OUTPUT_REVIEW.md](RPG05_KEYED_OUTPUT_REVIEW.md)。本文此前13次预算、未实测或资格过期段落是各自时点记录，不是当前12/14账本状态；原第12次失败不因修复改写为成功。
