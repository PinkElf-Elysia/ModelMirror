# R19 NPC权威合同威胁模型

## 保护目标

现有Runtime保持游戏状态转换唯一权威；Ledger只记录经验证的意图、裁决、因果和Runtime结果。任何模型、Agent、CLI输入或派生索引都不能直接修改Runtime或历史。

## 主要控制

- Policy是独立受信输入，Intent只能引用精确actor/node/action，不能自带权限。
- 每次新Intent必须匹配revision、head和Runtime snapshot hash；不匹配时零追加。
- 相同Intent canonical hash幂等返回；同ID不同内容fail closed。
- 连续revision、previous hash、entry hash、Ledger head和逐条Runtime重放共同验证历史。
- 有效拒绝写入事件但before/after snapshot hash必须相同；合同、身份或操作故障零追加。
- R19只签发projection manifest；记忆和关系内容由R21实现且必须可重建。
- projection artifact输入在复制和哈希前限制为16 MiB；manifest中的`byteLength`受同一上限约束。
- 合同禁止提示词、供应商字段、URL、绝对路径、凭据、底层异常和墙钟时间。
- CLI只向`C:\tmp`全新目录同父staging后单次rename；authority目录和输出父目录在操作期间发生身份换身时fail closed，失败不发布。

## 明确不由R19提供

- 纯函数expected-head检查不等于全局串行化。两个并发调用若都读取同一个旧head，均可形成有效但互斥的候选分支；R20宿主必须实现单一权威写者和唯一head提交。
- 哈希链不证明来源真实性；拥有全部文件改写权限的主体可以重算一条可重放的新历史。跨设备或敌对存储需要独立签名或可信head。
- 全量Ledger JSON接口适合合同验证、恢复和离线CLI，不适合长时间线的高频实时循环。R20增量层只能作为可丢弃执行缓存，必须保留从初始Runtime Session完整重建能力。

R19无网络、无模型、无Godot/Creator接线。逆序revert六个提交即可移除本轮代码；仓外CLI输出不会由Git回退删除。
