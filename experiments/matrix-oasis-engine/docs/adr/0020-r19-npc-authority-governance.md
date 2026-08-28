# ADR 0020：R19 NPC权威与Ledger治理

## 决策

Runtime Pack simulator继续负责唯一状态转换。R19建立独立Authority Policy、NPC Intent、Adjudication Result和append-only World Event Ledger；接受事件必须调用冻结Runtime，拒绝事件不得改变Runtime。Ledger重放从全新Session逐Action执行，不信任存储的after-state。

R19只支持现有Runtime Action。移动、发言、动态事件和自由payload不预留松散字段；后续轮次若需要，必须新增版本化合同。Memory/Relationship在R19只记录派生产物manifest，内容与reducer归R21。

## 后果

该边界牺牲本轮NPC表现能力，换取权限、幂等、并发和重放语义可被独立证伪。R20可在不复制世界状态的前提下提交同一Intent；R21可删除并重建派生索引。
