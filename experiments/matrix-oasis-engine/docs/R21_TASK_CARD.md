# R21任务卡：人格种子、记忆与关系派生状态

- `R21_BASE_SHA=cbb50f1095a51f2c32958ab4f7dd4e34dadfc2c2`
- 分支：`codex/matrix-oasis-r21-derived-state`
- worktree：`C:\tmp\modelmirror-matrix-oasis-r21`
- 版本：`0.21.0-r21`

R21承接R18路线中“人格、长期记忆与关系派生投影”的目标，资格profile固定为`matrix-oasis.npc-derived-state/1`，并按R19/R20已经验证的权威边界收窄实现语义。Runtime仍是游戏状态唯一权威；World Event Ledger仍是裁决历史、因果与来源证明唯一权威；R21只产生可删除、可重建、不可反向写入权威状态的派生产物。

R21.1只迁移治理、声明门和最小语义，不创建合同或运行时workspace，不修改R19/R20实现，也不提前实现R22对话与模型认知。后续实现只允许进入精确白名单中的R21目录和文件。

进入门：

- `R20_GODOT_ENTITY_BRIDGE_QUALIFIED`
- `R20_RUNTIME_REMAINS_AUTHORITATIVE`

退出门保持R18冻结名称：

- `R21_LEDGER_REBUILD_EQUIVALENT`
- `R21_MEMORY_DELETION_VERIFIED`
- `R21_RELATIONSHIP_PROJECTION_DETERMINISTIC`

上述标识只有合同、实现、证伪和人工验收全部完成后才能输出。当前`docs/V2_STATUS.json`为`r21-derived-state-in-progress`，`claimAllowed`继续为`false`并阻断至R25。

回退：逆序revert R21提交或停用未来R21 projection profile；R16 MVP、R19 Ledger与R20实体桥保持独立可用。删除仓外派生产物不删除Ledger，也不由Git回退自动完成。
