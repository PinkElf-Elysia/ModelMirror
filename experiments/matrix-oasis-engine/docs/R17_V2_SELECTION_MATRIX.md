# R17 第二版技术选型矩阵

## 决策摘要

| 赛道 | 推荐 | 备选/延后 | 结论依据 |
|---|---|---|---|
| Godot行为树 | 继续使用现有Runtime状态机基线 | Beehave延后；当前LimboAI运行包拒绝 | LimboAI 20次trace一致且运行兼容门通过，但实际包出现未批准CC-BY-4.0许可面，GDExtension二进制也无固定源码构建来源，因此硬门失败。Beehave在干净固定源码下由Godot 4.6.3加载GdUnit工具时解析失败；未证明是Beehave本体不兼容，也未完成20次语义trace，故只延后。 |
| 对话表现 | 继续复用现有原生Control | Dialogue Manager延后 | 受限fixture完成20次一致trace，但实际运行配置是Compatibility而非Forward+，且每次退出均报告资源泄漏；完整许可证与隔离面也未闭合，不能作为备选。 |
| 记忆 | R18先做World Event Ledger的确定性派生索引 | Mem0、Letta均延后 | Mem0的20次一致只证明SDK可调用测试自建HTTP接口；add/search/correct/delete/export与持久状态由夹具自身Map实现，`npm ls --all`依赖树也不完整。Letta仍只有固定源码身份，没有运行资格。 |
| 动画夹具 | R18不依赖动画资产 | Kenney、KayKit均延后 | Kenney页面标称1.0而固定下载包报告1.1，且只有idle/jump/run，缺walk/turn；KayKit尚无固定归档哈希。 |

## 采用和切换规则

- 行为树：R18不新增第三方依赖。只有候选在Godot 4.6.3完成20次语义trace、完整允许许可证闭包、可复现二进制来源和强制隔离后，才允许从现有Runtime状态机基线切换。
- LimboAI：只有建立不含CC-BY演示/标识资产的runtime-only源码构建，且每个平台二进制可复现时才重新资格；当前包不得引入。
- Beehave：必须先把GdUnit工具链与候选本体分离，证明完整套件与20次语义fixture均零退出，再评估更小运行面优势。
- 原生Control：继续是默认表现层。只有本地化、多分支作者体验形成可量化缺口，且Dialogue Manager受限Forward+ fixture无泄漏时才切换。
- Ledger派生索引：原始事件始终为权威源。只有真实本地记忆实现通过事件来源、时间、置信度、删除、重建和隔离门，才重新考虑Mem0或Letta；SDK传输fixture不构成记忆能力证明。
- Letta：仅在后续明确批准独立Agent服务、数据库和provider边界后重做资格；不作为R18前置。
- 动画：R18不因缺少动画夹具而扩张范围；取得固定来源和完整clip后另轮资格。

## R18边界

R18可以定义NPC意图、World Event Ledger、裁决结果、派生记忆和观察投影合同；不得提前实现AI人格、长期关系、动态任务、模型驱动世界事件或运行期供应商调用，也不得把R17已降级的第三方候选作为前置依赖。WorldX提供产品编排灵感，但不替代Concordia行动裁决、AI Town事务边界和现有R16权威Runtime。
