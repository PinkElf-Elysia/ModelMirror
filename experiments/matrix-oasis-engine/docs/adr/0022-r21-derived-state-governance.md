# ADR 0022：R21派生状态治理

## 决策

R21采用模块内、确定性、可删除重建的派生状态，不引入Mem0、Letta、Graphiti或其他外部索引作为生产依赖。Persona是可信静态seed；memory只记录actor自身已接受Action；relationship只使用显式精确Action映射的定向有界整数delta。

Runtime和R19 Ledger的权威边界保持不变，R20固定调度与Godot实体桥保持冻结。R19 Projection Manifest继续只承担身份绑定，R21必须自行生成并复验artifact，不能把manifest存在误当作内容正确性证明。

R18路线中的删除、修正和隔离在本轮采用更窄且可证的定义：支持整套派生artifact删除后从同一Ledger字节级重建；拒绝跨timeline输入；不实现选择性forget、correction或跨reset语义。任何更强能力必须新增合同版本和单独审批。

## 回退

停用R21 projection profile并删除派生artifact即可回到R20；Ledger、Runtime、R16 MVP和R20实体桥不受影响。Git回退不删除仓外派生产物。
