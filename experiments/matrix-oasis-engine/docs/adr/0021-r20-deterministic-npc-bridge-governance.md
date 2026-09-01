# ADR 0021：R20确定性NPC桥治理

## 决策

R20采用内部声明顺序固定策略与Node单写者，在隔离Godot wrapper中串行移动一个真实NPC。R19 Runtime仍为唯一权威，Godot仅在有效到达后请求裁决并镜像接受结果。

Beehave与LimboAI保留为备选证据，不引入依赖；R20需求不足以抵消版本锁、原生二进制和服务面成本。R16默认可玩路径保持不变。

## 回退

停用R20 profile并逆序revert七个R20提交即可恢复；R16与R19保持独立可用，仓外run不由Git回退删除。
