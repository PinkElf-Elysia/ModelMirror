# R18 第二版选型矩阵

## 结论

R18锁定了62个唯一候选、96个“候选×赛道”条目，并对13个唯一候选进行了仓外启动或审计。当前没有候选满足正式集成硬门，因此：

- `integration-recommended`：0；
- 七个可执行赛道均有2–3个下一步短名单；
- 已尝试的13个候选全部保持`deferred / evidence-gap`；
- 商业产品只作公开能力基准；
- R19从自有权威合同和Ledger边界开始，不绑定任何外部框架。

完整机器可读结论见`R18_DECISION_LANDSCAPE.json`，路线见`R18_ROADMAP.json`。本文件只给出决策摘要，不覆盖canonical报告。

## 分赛道决策

### 1. NPC认知、编排与行动裁决

短名单：

1. `world-event-ledger-baseline`：90分，证据缺口；作为R19自建权威边界，不是已完成实现。
2. `deterministic-runtime-baseline`：90分，证据缺口；保留现有Runtime为最终裁决者。
3. `sotopia`：71分，待执行；只作多Agent与社会行为评估备选。

决定：R19自建provider-neutral Intent、Ledger、Adjudication Result和replay合同。Concordia、AI Town、AutoGen、CAMEL、LangGraph、TinyTroupe等只提供边界参考，未证明可替代权威Runtime。

切换条件：相同Intent产生不同裁决、裁决前修改权威状态，或崩溃后无法从Ledger重建时，停止当前路径并重新评估下一候选。

### 2. 人格、长期记忆与关系状态

短名单：

1. `world-event-ledger-baseline`：89分，证据缺口；先定义可删除、可重建的派生投影。
2. `mem0`：70分，证据缺口；loopback SDK层已测，但OSS Memory引擎受未批准原生依赖阻断。

决定：R21前不引入记忆服务。Ledger是唯一权威来源；索引必须可删除并完整重建。Mem0仅在原生OSS路径、隔离、更正、删除和重建fixture全部闭合后作为适配备选。Letta、Graphiti、LangMem、Cognee及其他候选继续保留在长名单，不以README能力代替证据。

切换条件：更正、删除、session/timeline隔离或全量重建产生不同投影时，淘汰该适配路径。

### 3. 动态任务、世界事件与涌现叙事

短名单：

1. `world-event-ledger-baseline`：90分，证据缺口。
2. `deterministic-runtime-baseline`：90分，证据缺口。
3. `voyager`：71分，待执行；仅参考技能/反馈循环，不作为游戏世界权威系统。

决定：R23只接受“事件提案→裁决→原子提交”。WorldX、Concordia Game Master、AI Town、Generative Agents等用于比较事件注入和涌现叙事模式；WorldX不成为默认架构。

切换条件：提案在裁决前改变Runtime，或失败后遗留半事件时，当前实现立即失格。

### 4. Godot本地行为执行

短名单：

1. `deterministic-runtime-baseline`：89分，证据缺口；R20先接现有Runtime与Godot实体桥。
2. `beehave`：78分，证据缺口；Godot 4.6.3受控退出通过，尚缺20次trace和2/4/32/64 Agent负载。
3. `limboai`：75分，证据缺口；精确源码通过，但来源闭包不能证明预编译GDExtension。

决定：R20优先实现小型确定性策略桥。Beehave是纯GDScript备选；只有在统一负载和abort/timeout/blackboard门通过后才考虑集成。LimboAI必须先完成源码构建二进制身份和相同fixture。

切换条件：abort、timeout、黑板隔离、确定性trace或64 Agent profile失败时切换。

### 5. 对话作者体验与运行表现

短名单：

1. `native-control-dialogue-baseline`：91分，证据缺口；仅一次既有测试，不得享有内部基线豁免。
2. `dialogue-manager`：80分，证据缺口且归因未决；20次语义trace和受控退出完成，但Godot日志不干净。

决定：R22以前继续使用原生Control表现层。Dialogue Manager只有在Forward+日志干净、表达式/状态mutation/动态资源加载可封闭、reset与本地化fixture通过后才成为备选。Dialogic、Ink/Yarn适配保留为长名单，不提前引入作者格式。

切换条件：任何脚本表达式、状态越权、动态资源加载、reset或本地化失败都使候选失格。

### 6. 角色形体、动画与表现资产

短名单：

1. `kenney-animated-characters-retro`：85分，证据缺口；导入和300帧通过，但缺独立turn clip。
2. `static-character-asset-baseline`：81分，证据缺口；只能证明静态角色回退，不是动画方案。

决定：R20只使用固定、可追溯的动画夹具验证实体桥，不替换R16真实资产。Kenney补齐idle/walk/turn profile后可继续；KayKit、Quaternius等需固定归档、许可证、骨骼、脚底与AABB证据。

切换条件：clip、骨骼、落地、AABB、导入身份或300帧性能任一失败即切换。

### 7. 评估、重放、安全与可观测性

短名单：

1. `world-event-ledger-baseline`：89分，证据缺口。
2. `creator-qualification-baseline`：89分，证据缺口；仅一次既有资格测试。
3. `runtime-evidence-baseline`：89分，证据缺口；仅一次既有证据测试。

决定：沿用R15/R16的运行证据链，并在R19加入Ledger事件、裁决和状态diff。GameCraft-Bench、SOTOPIA、AgentMemBench等只补充fixture和评估思想；模型裁判不能替代确定性运行结果。

切换条件：固定输入重放、状态diff、逐节点证据或canonical报告存在缺失/漂移时，资格失败。

### 8. Creator一句话生成与商业产品基准

该赛道不产生可执行短名单。WorldX、Godogen、AI Town及Inworld、Convai、NVIDIA ACE、Rosebud等只按固定公开资料比较用户入口、表达力、延迟、成本、所有权、隐私和导出边界。本轮未注册、试用或调用商业API。

决定：R24继续在R16 Creator与权威Pack/Runtime链上编排第二版配置，不复制商业产品不可验证的服务面，也不让一句话体验绕过R19–R23的合同和证据门。

切换条件：官方公开能力、价格、所有权、隐私或导出声明发生实质变化时重新审计。

## R19–R25冻结路线

| 轮次 | 唯一目标 | 最小退出门 | 回退 |
|---|---|---|---|
| R19 | NPC Intent、World Event Ledger、裁决、Memory Projection等权威合同 | fail-closed裁决、Ledger重建确定、canonical合同 | 逆序移除合同与fixture，R16继续独立运行 |
| R20 | 固定策略NPC与Godot实体桥 | 多Agent trace确定、Runtime仍权威、实体桥资格 | 禁用R20 profile并移除桥 |
| R21 | 人格、长期记忆与关系派生投影 | 删除/更正/隔离/全重建等价 | 删除派生索引，从Ledger重建或禁用 |
| R22 | 受限AI对话与认知循环 | 预算、超时、降级、输出裁决与当次审批 | 退回固定策略对话 |
| R23 | AI任务与世界事件提案 | 原子裁决、回滚、重放等价、无半事件 | 停止提案入口并回放最后已提交Ledger |
| R24 | Creator第二版编排 | V2 profile资格、失败保留MVP、审批内容绑定 | Creator切回R16默认profile |
| R25 | 多案例真实资格与第二版声明 | 正确性、体验、安全、成本和商业价值均人工通过 | 保持`claimAllowed:false`与R16公开MVP |

## 证据边界

- 来源锁SHA-256：`f5e27479985a6de2a04055c4d5f97a99b687847484fd7e668a508ccd618b985e`。
- Catalog SHA-256：`e47791fd90ba0776bf90c907fc52ed57f7bf47595bb362c858152255be157222`。
- 资格证据集SHA-256：`3d448f7760a08d63c0073bf37fa3269300757c658c81c97f37ef8ab9b483cbd0`。
- 最终Landscape SHA-256：`65ed29270ec77aa2e64401f591e5f7fb58e93acb65456c4bf141e42195813a00`。
- Roadmap SHA-256：`8ecf5d2a5b2e4f5fea3ac64960949ce56b7a095651bcaba96bef42a4b927b428`。
- 本轮没有OpenAI、Marble、Meshy或商业产品调用，没有读取供应商凭据，没有启动容器。

R18只完成选型底座和路线冻结，不代表第二版功能完成。`claimAllowed`继续为`false`，声明门固定在R25。
