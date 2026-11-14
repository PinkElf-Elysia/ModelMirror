# R1 会话、成员与消息基础

## 开工契约

基线 0cb019df01642d71a5eef55eeee31d1086ab037d；分支 codex/rpg-character-r1；独立工作树 C:/tmp/mm-cr1，短数据根 C:/tmp/mm-cr1-data。远端主线开工时相同。原角色工作树五份文档改动不带入。

仅新增 character/conversation/ 内部模块、测试及本文档；不修改旧宿主、世界合同、兼容哈希、HTTP、UI、网关或迁移。无真实调用、部署、提交、推送。风险为本地文件和测试子进程；复用 SessionRecords 原子写入，数据独立，回退为停止本轮自有进程并保留数据/证据。

分批验收合同、存储、旧适配和兼容。至少运行新测试、旧角色/世界恢复、共享宿主、Windows 短路径 mock、Linux 禁网网关、相关前端和两个构建。既有帮助失败单列；首次失败日志不覆盖。

## 交付与入口

实现位于 `experiments/ai-rpg-engine/character/conversation/`：

- `contracts.mjs` / `types.ts`：内部版本合同、操作校验、稳定 ID、参与区间与视图。
- `store.mjs` / `runtime.mjs`：复用 SessionRecords 的持久化宿主与独立运行指纹。
- `legacy.mjs`：经过旧 CharacterSessionHost 验证的只读适配。
- `*.test.mjs`、`test-support.mjs`、`archive-support.mjs`：原创中性测试、精确 Git 基线生成与恢复验证。

没有 UI、HTTP 注册、发言调度、模型调用或新网关消息合同。多 Agent、旁白运行、世界事件、行动结算和通用 Harness 不在此交付中。外部关联固定为 null，不能授予能力。

## 合同与持久化

内部格式为 `modelmirror.rpg.character-conversation/1`。每个会话以单一原子记录保存不可变操作事件；成员、消息和修订号由严格重放产生，避免多份派生状态发生部分提交。它复用 SessionRecords 的存储 envelope、CAS、所有权锁及恢复，不另造事务存储系统。

- 会话 ID 从创建 operationId 派生；成员和消息 ID 从会话及对应操作派生，名称不参与。重启和相同操作重放稳定，同名、同模板实例彼此独立。调用方必须为新会话生成新的 operationId。
- revision 从 0 开始；消息 sequence 从 1 开始、只随成功追加消息增长。成员变化不制造聊天消息。
- 初始玩家在创建时冻结引用与称呼，不能离场/替换；本轮新增成员均为 AI 角色。完整玩家档案、多人类玩家及玩家身份切换不在 R1。
- 每个角色固定具体 workId/versionId/configurationHash 和显示名；每次加载重新核验封存版本及资产，不跟随草稿或 latest。
- 活跃成员区间为 `[from, until)`，until=null 表示在场。加入从下一条消息开始，离开在下一条消息之前结束；无消息的离开/重入允许空区间。
- 开场由宿主从该成员的封存卡和玩家快照展开。每个角色只能在其第一条发言位置追加一次；重入不重插开场。
- 消息来源目前只有玩家输入、明确的测试夹具和版本固定的开场。没有“模型回复已验证”的伪造入口。
- 私有资料是人工追加的成员私有文本，不是自动记忆抽取系统；归属会话与成员，不写入模板或交换导出。

工程上限：每会话 1000 个操作事件、32 个成员（含玩家）、每段正文 200000 个 UTF-16 code units；沿用既有 JSON 复杂度约束和 32 MiB 存储记录上限。达到上限明确拒绝，不静默裁剪。R2 的产品成员上限和调用预算需要单独收紧。

## 内部服务接口

从 `store.mjs` 导入 `ConversationStore`。`open({directory, works, fixtureMode=false, recoverDeadOwner=false, fault?})` 接受已打开的可信 AuthoringStore；works 由调用方关闭。directory 必须为独立绝对路径，沿用底层路径与单所有者约束。fault 仅用于宿主测试注入，不来自材料或 HTTP。

打开后显式调用 `recover()` 才能读写。所有写操作采用 `{operationId, expectedRevision, payload}`；创建的 expectedRevision 为 null，其他操作为当前整数修订号。返回 receipt 包含原操作的会话 ID、提交修订和产生的成员/消息/资料 ID，不夹带其他私有内容。

| 方法 | payload / 参数 | 行为 |
| --- | --- | --- |
| create | `{player:{id,name}}` | 创建会话及初始玩家 |
| join | `{reference:{workId,versionId}}` | 新增独立 AI 成员；由作品存储核验角色版本 |
| leave / rejoin | `{memberId}` | 结束或开启 AI 成员参与区间，不删除既有记录 |
| append | `{speakerId,content,visibility,addressedTo,source}` | 玩家输入或显式夹具消息；无模型结果入口 |
| appendOpening | `{speakerId,openingIndex}` | 校验并展开固定版本开场，不接收自定义开场正文 |
| addPrivate | `{ownerId,content,source:{kind:'manual',reference}}` | 活跃成员的人工私有资料，只追加 |
| readOwner | `conversationId` | 可信本地宿主的完整管理视图，不能作为模型上下文 |
| readMember | `conversationId, memberId` | 当前在场成员的过滤视图 |
| recover | 可选 conversationId | 核对本地未决写入；不发模型请求、不追加下一步操作 |
| close | 无 | 排队操作结束后关闭本模块存储，不关闭借用的作品存储 |

append 的 source 为 `{kind:'player-input',reference:null|string}` 或 `{kind:'fixture',reference:null|string}`；reference 只接受已有 isId 规则。player-input 的发言者必须是本会话玩家。fixture 仅在宿主显式 fixtureMode=true 时可写；持久化模式不匹配时拒绝打开，不能把夹具模式悄悄用于正式宿主。

visibility 为 `{kind:'public'}` 或 `{kind:'members',memberIds:[...]}`；限定接收范围必须包含发言者，所有成员引用须属于本会话。addressedTo 是单独的点名列表，不能改变可见范围或触发生成。新消息不能以离场成员身份追加。

重复 operationId 只有完整请求意图相同才返回原 receipt；即使后来会话已有新修订，也不再写入。不同请求意图返回冲突。两个竞争写入不能同时使用同一修订提交。提交失败后宿主停止普通读写，必须明确恢复；重启也不自动恢复。提交后结果未知时只核对已保存的本地操作，不能继续另一个会话的写入来绕过未决状态。

## 可见性不是登录授权

所有服务方法都是可信宿主内部接口，memberId 不是认证令牌。R1 没有新增多租户、用户登录或网络鉴权；R2 注册外部入口时必须由受信请求上下文决定操作主体，不能让浏览器用任意 memberId 获取成员视图。

成员视图采用 `modelmirror.rpg.character-member-view/1`：

- 仅包含参与区间内、且通过 public/限定接收者检查的消息；重入会合并此前参与区间，不补授离场期间历史。
- 自己可见完整成员绑定；其他当前成员以及可见旧消息涉及的成员只显示 ID、类型和名称。不会返回他人的绑定、私有资料、操作账本、总消息数、内部参与区间或全局修订。
- 消息先过滤，后投影；不输出隐藏消息的正文、来源或诊断，也不提供隐藏消息序号。消息 ID 和可见消息顺序保持稳定。
- 私有资料仅属于该成员；同一模板的另一个实例不继承。

旧标准角色卡的导出仍只读取作品定义，新会话数据与操作记录不进入 AuthoringStore 或交换格式。

## 旧单角色只读适配

`LegacyConversationReader({host,works})` 必须接收已有 CharacterSessionHost 和 AuthoringStore 实例。`readOwner(sessionId)` 先调用旧宿主的完整校验，再核对原封存配置；不从浏览器或任意 JSON 构造可信会话，不直接打开旧存档文件。

- 管理视图为 `modelmirror.rpg.character-legacy-view/1`；成员视图为 `modelmirror.rpg.character-legacy-member-view/1`。两者均 readOnly=true，不是新存储记录或网关格式。
- 稳定映射两个成员，消息 ID 由旧 sessionId 与固定位置派生；续聊不改变旧消息 ID。不会虚构旧消息时间或把角色名称当 ID。
- 开场独立标为 legacy-opening；完整回合为 legacy-turn。示例不进历史；未知/进行中请求只提供状态，不将输入或部分回复伪装成已完成消息。
- 私有记忆保留原条目及来源，映射给角色成员。管理视图保留旧 settings；成员视图保留 memoryEnabled。能读取保存的条目不代表它已启用或已被选入模型上下文。
- 读取不调用 send/recover、不修改原绑定或运行模式，不重写 ID、修订、请求和回执。旧聊天入口继续走旧宿主。

## 运行指纹和后续版本

新宿主仅登记自身模块及直接、间接使用的旧内核文件。旧单角色与世界宿主、精确兼容清单保持原字节；未将新模块注册进旧指纹。

本模块源码改变时，已打开宿主拒绝继续操作；重开后不匹配的旧记录也拒绝读取。该保护检查实现一致性，不宣称能防御有权同时改源码和存档的本机写入者。没有任意源码升级能力或自动迁移。

R2 不能直接修改本版本后更新存档哈希。应保留版本化读取/运行路径，或另行提出精确兼容方案并用固定旧版本验收；需要迁移时仍须单独授权。修改旧 character/card.mjs、document.mjs 等既有指纹依赖前必须重验旧世界矩阵。

## R2 接入条件

1. 从可信作品版本创建会话和成员，宿主保存 memberId；由用户明确选择在场成员。
2. 取得该成员的过滤历史及固定版本，分别装配其人设；先检查旧记忆启用设置和条目启用/触发规则，不把管理视图或所有私有条目直接拼入提示词。
3. 独立实现群级预算、用户操作意图、请求幂等、未知结果恢复和停止语义；在派发前固定成员、模板版本和上下文版本。
4. 设计新的受控群聊消息合同及可信结果采纳入口。当前 append 不支持 model-result，不能通过 fixture 来源或放宽旧网关合同接入。
5. 对迟到回复核对原成员、版本、可见性和请求；成员离场或权限变化不能被迟到结果绕过。该流程尚未在 R1 实现。
6. 再实现群场景、群开场选择、2–3 角色手动聊天与页面；不假设 Agent 化工作树已完成。

R1 完成只证明存储、标识、隔离和适配基础；没有证明群聊生成、人物长期稳定性或生产恢复。结果和命令见 [R1 验收报告](CHARACTER_R1_ACCEPTANCE.md)。
