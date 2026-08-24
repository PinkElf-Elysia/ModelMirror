# R17 第二版架构参考审计

## 结论

R18 不应复制任何候选项目的完整运行时。现有 R16 Runtime、Scene/Spatial 合同和 Godot 世界继续是唯一权威链；外部项目只分别贡献可替换能力：LimboAI 提供行为树执行，原生 Control 继续承担对话表现，记忆先实现 World Event Ledger 的可重建派生索引，Mem0 仅作为语义检索备选。

## 交叉审计

| 来源 | 可借鉴边界 | 明确拒绝的耦合 |
|---|---|---|
| WorldX | 阶段化生成、角色/事件职责、时间线和失败反馈 | 2D 场景、随机状态变更、生成器与 Runtime 混为一体 |
| Concordia | Entity/Component 拆分；行动先形成提案，再由 Game Master 裁决 | 模型仿真直接进入帧循环或覆盖 Godot 状态 |
| AI Town | tick、共享状态事务、会话成员和输入输出投影 | Convex 云状态成为 standalone 权威源 |
| Generative Agents | 事件、派生记忆、重要度与反思分层 | 自由文本记忆成为事实；静态存储冒充更正/删除语义 |
| TinyTroupe | 结构化人格片段和固定输入评估 | Python Agent 对象或模型调用进入 Godot Runtime |
| SOTOPIA | 行为执行与质量评估分离 | 模型裁判分数替代运行期正确性 |
| Graphiti | 未来多跳时序关系检索候选 | 在简单 Ledger 索引尚未证明不足前引入图数据库 |
| Voyager | 课程难度与失败证据归档 | 动态代码生成、下载和执行成为第二任务系统 |

WorldX 的“一句话生成”产品路径与本项目接近，但其结论必须同时满足 Concordia 的裁决边界、AI Town 的事务边界和 R16 的权威合同，因此不构成默认架构。

## R18 合同约束

1. NPC 只输出受限意图；意图由权威事件合同裁决后才可改变世界。
2. 原始事件、派生记忆、人格摘要和表现文本必须可区分、可追溯、可单独删除或重建。
3. 记忆实现只能是 World Event Ledger 的派生索引，不能拥有世界事实。
4. 行为树黑板按 NPC、session 和 timeline 隔离；切树、超时和失败必须产生可观察结果。
5. 对话表现默认复用原生 Control；只有出现已测的本地化分支表现缺口，才重新资格 Dialogue Manager。
6. R18 不依赖动画夹具、Graphiti、Letta、容器或真实模型调用。
