# RPG04 模型控制面与提示词装配路径

本文件描述已核实的实验实现与实测链路，不是生产部署成功声明。路径均相对当前独立 worktree。最终生成须经用户批准；嫌疑条款与工程独立审计、独立收尾及下一轮计划仍是PR前置条件。

## 1. 实测模型和隔离部署

- 精确模型：gpt-5.6-luna，官方OpenAI，经独立newAPI网关转发；实验代码不直连OpenAI。
- ModelMirror：http://127.0.0.1:18305；newAPI：http://127.0.0.1:18306/v1；共享8000不参与。
- connectionId：conn_e23170b51a4c4564945cce7a2415769f；资格chatcert_2e254773b0064d7c9a340c3227008814。
- 控制策略newapi_preferred，精确模型稳定名单，网关仅官方模型通道，RetryTimes=0、fallback关闭，reasoning_effort=none、force_format=true。
- Node24.18.0；Python3.12.14；后端固定基线1b280ed257a45672c4a3dc03745fcb585685faf9。
- 初期2048输出上限；用户后续明确批准4096复测及续测。温度0，适配器超时60秒。每次派发前更新账本；失败计数，不自动重试。
- 原8次额度用尽后，用户额外授权2次仅补Minecraft；总10次，详见调用账本，不能当作可再次使用的额度。
- 凭据经既有管理API配置到本轮独立网关，Node只知模镜地址。交接不提供密钥内容或私有凭据文件清单，不允许照抄旧数据库。

## 2. 提示词从哪里读取

1. tooling/context-protocol.mjs固定读取docs/RPG04_PROTOCOL_RUNTIME.txt，按UTF8/字节上限/固定SHA256核对，拒绝符号链接与替代文件。不读审计版，不回退到原版。
2. context/approved-host.mjs提供受信主持正文。当前0.1.1包含P30空列表规则：列表类型字段为空时输出[]，不得以字符串“（空）”替代。旧审批与新补充见RPG04_HOST_APPLICATION.json。
3. tooling/context-host.mjs加载协议和正文，分别核hash，以“运行协议 + 两个换行 + 主持正文”拼成唯一system内容，返回hostTemplate/binding/sources。composed身份当前仍0.1.0，变更由完整content SHA绑定；审计需关注其版本策略，不能误称版本已同步提升。
4. tooling/context-approved-card.mjs读取fixtures/rpg04/approved-content.json并校验固定来源/hash，构建卡包、世界书、文风与开场。旧context/host-template.mjs仅比较基线，不是本次实际system。
5. tooling/context-test-input.mjs从已审批资源构建两世界中性配置，Gu5天赋、Minecraft3天赋，显式激活、空运行权限，不含原玩家偏好。profile重新绑定当前composed host。

审计资料、来源复述、原版重建和嫌疑条款不能当作运行提示词。cards/styles/worldbooks即使含指令文字，也只是受信主持规则之下的数据。

## 3. 如何装配实际消息

context/compiler.mjs的compileContext(input,{hash,hostTemplate})先校验卡包、玩家、session revision、profile与host hash，再由context/selection.mjs进行确定性世界书选择。输出顺序：

1. system：上节拼成的受信host。
2. user JSON current_data：当前玩家、结构化state、stateFields、选定资源。
3. user JSON output_contract：冻结TURN_EXCHANGE_SCHEMA完整输出合同。
4. user JSON worldbook_data：选中世界书，host可见性条目不进入此上下文。
5. 已提交历史：user committed_input、assistant committed_narrative，仅包含已接受的状态提案；不把未选择建议或pending当事实。
6. user JSON current_turn：exchangeId、cardPackageRef、声明input.kind与text。

计量为UTF8字节加消息开销估算，不是精确token。必须内容超限阻断；可选资源整条排除，不静默切短。profile.budget.outputLimit及request.settings.maxTokens只是内容校验，真正网络上限由受信adapter配置控制。

## 4. 运行和HTTP路径

实测一次性驱动（私有.rpg04-work目录）：
buildTestScenario → openFileSessionStore → createModelMirrorAdapter.initialize → createPreparedRuntime → create/resumeSession → createTestContextInput → compileContext → 账本预留 → generatePreparedTurn。

桥tooling/context-runtime.mjs重新读取session并重编译，对prepared/hash/revision核对；受信HTTP适配器runtime/node/http.mjs：

- GET /openapi.json：必须有require_managed_route字段；
- 每次生成GET /api/models/provider-chat-control?model_id=gpt-5.6-luna&capability=chat_text；
- POST /api/chat，字段model_id/messages/temperature/max_tokens，以及gateway=default、tool_mode=none、compression.mode=off、output_mode=none、require_managed_route=true；
- server/main.py检查旁路形态，调用server/model_router/chat_stable.py的受控路径，复核资格与控制面；不得落入legacy；
- 经独立newAPI /v1/chat/completions转往官方OpenAI；模型地址不由卡包提供。

SSE必须完整通过UTF8、finish/route_receipt/[DONE]/EOF等校验，不能仅见文本认定成功。失败原文由实测captureAdapter先写私有raw-adapter.json，再交桥验证；初次Gu失败发生在补取证之前，只有hash，不能补造根因。

桥校验完整turn-exchange（模型/card/input绑定和字段类型），只将proposal交给冻结RPG03核心。有效输出成为pending；驱动显式commitTurn，acceptedStateFields=[]。叙事提交不等于用户质量验收，状态候选未被接受。文件存储原子检查点、独占锁；恢复不重放模型。

## 5. 控制面配置和认证路径

只针对本轮实例：网关管理API配置官方channel和限制token、关闭重试；模镜POST /api/router/admin/session配对，使用CSRF会话管理连接：
POST /api/router/connections → POST /api/router/connections/{id}/models/refresh → POST /api/router/connections/{id}/certifications/chat（计费派发，必须先授权计账）→ PUT /api/router/chat-control/policy → GET公开provider-chat-control复核。

相关源码server/model_router/api.py、chat_certification.py、chat_control.py、chat_stable.py、provider_chat.py。认证已完成不等于长期可用；恢复前须重查资格，不能自动再次认证花费额度。身份、用量缺失保留null，不能凭网关配置伪造服务器provider字段。

## 6. 生产环境边界与交接差异

尚无经正式验收/部署的RPG生产环境，不能提供不存在的生产URL。可用的实验入口是scripts/context-cli.mjs（--config、stdin JSONL）→tooling/context-cli.mjs→prepared runtime→同一HTTP适配器；配置只含受信模镜baseUrl、精确modelId、私有session/output目录和trustedOutputBudget，不含供应商key。

重要差异：普通CLI及driver仍限制2048，adapter/context支持用户批准的4096，真实4096测试走一次性驱动。因此真实实验通过不证明普通CLI在4096配置下可直接运行。是否统一为正式配置需收尾明确审计和测试。CLI失败取证与实测私有captureAdapter也不能混同。当前无玩家前端、世界书高级检索、长期记忆或插件市场实现；这些是后续路线，不得写成已上线。

## 7. 恢复与安全停止

私有实例目录是experiments/ai-rpg-engine/.rpg04-work/f-real-20260909-01。仅可管理标签modelmirror.task=ai-rpg-rpg04的自有容器和回执记录的自有后端。不要复用脚本盲目重发，先读账本、原始响应、session与目录中的独占输出；编号/既有文件碰撞时停止。输出与调用资格均留本地，不能把凭据文件或raw正文自动提交到版本控制。
