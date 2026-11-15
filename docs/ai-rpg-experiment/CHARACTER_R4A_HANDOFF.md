# R4a：上下文与参数接口交接

## 范围与版本

原实现基线 `8ed5ed55`，最新组合基线 `aebda0a1`，新增代码位于 `character/long-context/`。旧单聊、群聊、R1、R3b 合同及精确兼容表保持字节不变。只复用本仓库实现，未引入第三方角色库、tokenizer 或 GPL/AGPL 实现。历史实现阶段零产品模型调用；其后 8 次单独授权的真实验收已获用户接受，见 CHARACTER_R4A_REAL_ACCEPTANCE.md。本次收口不追加调用，未提交/推送/部署。

R4a 使用 `modelmirror.rpg.long-context-ledger/1` 与独立 `character-long-context-v1` scope。原文/候选/回执状态转换复用 R3b `apply`，R4a 的参数、预览与来源记录保存在独立事件包中，不向旧严格请求对象塞字段。每份续聊副本有独立 family 身份；请求 ID 不因原来源相同而碰撞。

`Sources` 只接受已验证的 LegacySources / RevisionStore。复制当前有效历史、成员边界、身份、卡片版本、资料依赖；R3b 已消耗费用也从可用来源额度扣除。原记录仍由旧宿主读写，不做格式迁移。未解决请求阻断创建及发送。新建会话可在创建后进入该续聊入口；没有重造角色后台。

## 上下文合同

`plan({source, branch, targetId, model, policy, inputMessage, exact})` 为确定性函数。模型在调用前已完成能力/参数解析。输出 `context-plan/1` 含来源消息 ID/修订、配置摘要、实际 wire messages、估算、容量上界、输出预留、费用预留、纳入/省略原因及哈希。

先按成员参与区间/接收范围过滤，再应用窗口和容量。单聊以完整回合、群聊以完整消息为单位；固定开场默认开启。无开场不补造。先去示例，再去较早历史，最后缩小角色书预算。宿主规则、角色定义、身份、当前输入、任务/后置指令、有效确认资料不可暗中删除；不足则零派发。

估算使用 Unicode=1、ASCII=0.3；容量和费用保护使用 UTF-8 字节加消息/宿主开销上界，均非真实 tokenizer 计数。实际供应商用量与原始回执单独保存；上界不是账单。角色书沿用已有关键词语义，仅扫描过滤及裁剪后的历史和当前输入。其他角色私有资料、不可见消息及其来源 ID 不进入计划诊断。

精确重生成冻结原模型、参数、上下文。显式修改被拒绝；新窗口不会偷偷改变精确请求。用户可从消息位置另建分支按新条件续写。

## 参数与 API

`Parameters` 从可信模型清单与现有 ControlPlaneModelControl 解析参数意图。仅供应商声明且可编辑的字段暴露范围；无依据时不编辑。页面支持数值、整数、枚举、布尔及文本列表；约束与冲突由既有严格参数合同校验。

继承续写页的 cookie、CSRF、loopback Host/Origin 和命令白名单。新增：

| 接口 | 输入/效果 |
|---|---|
| GET /api/capabilities | 安全能力投影，不含凭据或授权写入口 |
| GET /api/context | 按请求读取冻结计划与实际用量；刷新后不重新装配 |
| POST /api/import | operationId + expectedRevision:null + reference；创建独立 R4a 副本 |
| POST /api/preview | familyId + operationId + expectedRevision + payload；不派发、不占额度 |
| POST /api/settings | branchId + targetId + settings；显式保存分支/成员默认 |
| POST /api/generate | 同一意图加 previewHash；重新核验配置并匹配预览后才准备请求 |

`settings={policy:{format,historyLimit,pinOpening,bookTokens},parameters:{modelId,values}}`。默认仅本次；设为默认要独立 CAS 写入。派生分支复制创建时默认快照。准备后只使用冻结参数；恢复不读取当前默认值。HTTP 不接受完整上下文、价格、授权或模型对象，也不开放 settle。

## 预算与恢复

`ParameterBudgetGuard` 为新的 `shared-parameter-budget/1`，任务授权不可变，认证后的配置追加登记。模型 ID/选择归属/冻结价格必须匹配可信清单，参数选择由现有控制平面再次核验。配置登记不增加额度。

已有 v1 账本通过 `leaseLegacyBudget` 独占所有权、严格旧验证和只读快照接入：拒绝 pending，保留原请求、原价格、源限制、历史占用和所有预留，不升级/重写旧文件。新台账按旧占用加新占用检查同一任务及来源限额。旧入口、R3b 和 R4a 都必须经同一个新 guard；不允许同时保留另一个可派发旧 guard。

`openContextControl` 与 `startContextWorkspace` 是可信宿主入口，默认真实额度为零。迁入已有工作台时必须先停止原宿主并取得独占租约，调用方提供已验证模型与价格、不可变任务授权及旧预算目录。旧账本另须显式提供原始策略，不能传入新授权代替旧授权校验。仅拥有代码不等于部署或调用授权。

预览 → prepared（原子写入玩家消息、意图和预留）→ dispatching → completed/failed/unknown。回执后只生成候选，采用后进入有效历史。未知请求保留额度，只查原请求，不因 absent 自动重发；只有可证明未派发的 prepared 才可关闭。回执采用中断通过同一请求恢复，不重复候选或计费。

## 使用与回退

离线启动：`node character/long-context/launch.mjs <独立数据目录> <起始端口>`。四端口依次为角色工作台、群聊、原续写、R4a；仅 loopback，使用原创中性夹具，无真实网关。已有材料或证据不要作为启动目录。

回退停止本轮进程并去掉独立导航即可；保留 R4a 数据。未知运行描述符/源码漂移继续拒绝，不能机械改哈希让旧数据通过。

## 后续条件

R4b 可在 R4a 人工页面验收后规划，沿用资料的 owner、可见边界、依赖修订、prefixHash 和有效/失效/待确认状态；不得直接把模型提案当事实。R4c 的自动提案、真实流式、中止和部分结果另行确认。Agent/世界/Harness/RAG 不在此接口授权内。

设备项仍延期：原生文件选择器取消、真机软键盘/触控/系统返回。已验证一次 DeepSeek 真实处理中完整刷新，无重复派发；其他供应商和断网/崩溃组合仍待专项验收。离线模拟不能替代。真实模型调用前须另行确认材料、模型、价格和推理费用上界、次数/金额、共享额度及停止条件。


## 预算交接修订

宿主接口增加：

- `openContextControl({legacyDirectory, legacyPolicy:{models,authorization}, models, authorization, ...})`
- `startContextWorkspace({legacyBudgetDirectory, legacyBudgetPolicy:{models,authorization}, ...})`

两个 legacy 参数必须同时出现。旧模型包含原价格快照，旧授权包含原 initial；仅用于严格验证旧账本，不改变旧文件。外层 models/authorization 是当前可信配置与单独获批的任务总授权，不由浏览器填写。

交接要求新授权 initial 与旧 initial 完全相同，新 calls/microUsd 不小于旧授权上限。历史请求占用只由旧账本另行累计，不能把它再加进 initial；也不能清零 initial 或缩减原未用额度。旧来源限额与按来源占用完整继承，新任务额度增加不自动增加来源限额。

新账本绑定新授权和旧账本哈希；重复打开使用相同快照。不同参数配置可按既有规则登记，但旧记录不按新价格重计。旧账本有 claimed 请求时拒绝交接；新未知请求重开保留占用，仅查原请求。原入口、R3b、R4a 使用同一个 guard；独占租约阻止另一个旧派发器同时接管。

此修订自身不授予新增真实额度。离线结果及首次环境失败见 CHARACTER_R4A_BUDGET_HANDOFF.md；后续单独授权的真实验收已验证原入口与 R4a 共享预算。收口后没有剩余的自动续跑授权。
