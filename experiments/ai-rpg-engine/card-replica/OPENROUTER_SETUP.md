# OpenRouter Gemini 对比配置（2026-09-17）

本文保留激活前配置快照；当前已认证并返回一份截断输出，累计4/4，最新状态见 OPENROUTER_ACTIVATION.md。下述“尚不可派发”描述的是准备阶段。

## 本批范围和结果

仅 card-replica/**；不改主工作区.env、父仓源码、旧提示词、旧真实输出或共享服务。新增准备工具和脱敏测试，不新增依赖，不创建公开接口，不迁移会话。凭据在进程内部读取，写入的只有来源引用和存在性；不输出密钥片段/指纹，不复制.env。风险边界是本机凭据读取及无鉴权公开目录GET，没有Provider认证或生成POST。

开始前分支codex/rpg-card-replica、HEAD 52f78f5edb6e59b38ae952ac836c23d273caf2e6，原37项DELIVERY匹配。原STATUS/DELIVERY/账本在.local/comparison-openrouter-20260917/before-*保留。首次在仓库根误用模块相对检查路径失败；改在模块目录运行通过，未掩盖路径错误。

**已配置，尚不可派发。** 当前模块里的GPT适配器固定为gpt-5.6-luna，未被冒充为Gemini适配器；18305受控路由及18413审阅宿主未切换。此次需求完成凭据存在性检查、精确模型/端点选择及角色请求准备，不把“key存在”说成“鉴权通过”。下一份真实调用前仍须完成受控路由激活、资格与预算迁移、实际出站验证；不能直接运行旧tools/real.mjs --generate。

## 凭据与模型

内部读取 C:/Users/21547/Documents/模型浏览器/server/.env 的 OPENROUTER_API_KEY，存在；根.env没有同名有效值，无冲突。主工作区文件未修改。后续仅服务端按来源读取，不进入浏览器、角色正文或证据文件。

精确模型google/gemini-3.8-flash；固定google-ai-studio标准端点，only/order仅该端点，allow_fallbacks=false，require_parameters=true。没有选latest、batch、flex或自动备用。模型及参数依据实时公开GET https://openrouter.ai/api/v1/models/google/gemini-3.8-flash/endpoints，原响应存catalog.json。查询没有Authorization，不外发用户角色，不占生成额度。首次沙箱内网络访问失败，获授权的外部只读查询成功。

资料：https://openrouter.ai/google/gemini-3.8-flash ；https://openrouter.ai/docs/guides/routing/provider-selection ；https://openrouter.ai/docs/guides/best-practices/reasoning-tokens 。

## 已冻结的对比输入与差异

.local/comparison-openrouter-20260917/：
- character.txt：用户给定角色卡正文逐字保留，含空格和标点，没有重新经UI模板生成。
- profile.json：不含密钥的模型、凭据来源、参数、缺口、预算及hash登记。
- prepared-request.json：尚未派发的完整请求草案。
- catalog.json：公开参数/端点目录快照。
- before-*：本批修改前的状态和账本等快照。

角色：龙婉翩，女性，中国上海，表世界；用户已删除私密部分。头部“2000年代/约2008年/约18岁”和背景“2010年代”并存，**原样保留，未纠正或推断年龄**。角色hash：33766118dc40145e1343b69577067860a6cb2b479f9cac3bdee055f1078681f7。

准备的玩家输入沿用上一轮“开始这一世。”，仅作为未派发草案。首轮装配仍按既有合同：完整官方system + 角色原文及输入。前后置词仅用于续轮；世界书仍全关。未修改官方任何文本或加入调优句。与原站是否采用同一首轮装配、输入及思考档位仍需对齐。

发往OpenRouter的草案参数：temperature=.70、top_p=.80、max_tokens=8192；transforms=[]禁止启用middle-out。端点未声明支持top_k、presence_penalty、frequency_penalty，故不发送。context8192为界面设置，不截断完整提示词或历史。thinking_budget=0不能按参数名直接照搬：目录列出reasoning不等于支持零预算，**未设置reasoning，实际会使用何种默认思考行为尚未验证**，不得宣称和原站等价或思考关闭。

## 验证、预算与后续

node --test tests/openrouter-config.test.mjs：3通过；验证凭据只暴露存在性、错误不含密钥、角色原样、完整官方system、禁止回退、精确端点及参数缺失拒绝。
node tools/configure-openrouter.mjs --prepare：成功，仅内部检查key和公开目录GET，0生成/认证。文件用wx防止重复运行覆盖既有配置。
npm.cmd run verify：22测试通过，TypeScript及生产构建通过；不是Gemini真实推理证据。

本卡预算仍已用2/4、剩余2。CALL_LEDGER旧条目及模型作用域原样保留；用户对slot2的反馈另记STATUS，没有将“已阅但不公平/不认可”写成质量通过。Gemini启用前需要将新模型作用域与已有消耗衔接，不能新建4次额度或冒用GPT资格。

回退本批新增配置工具/测试与文档，恢复before-STATUS；保留character/profile/catalog和账本历史，主工作区.env无需回退。无Commit、Push、PR、Merge、Deploy、Release或Publish。
