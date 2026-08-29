# R19 NPC权威、Ledger与裁决

R19在现有Runtime之上增加一条窄而可重放的NPC输入边界。Runtime仍是游戏状态转换的唯一权威；NPC只能提交已经存在的Runtime Action，不能写变量、创造事件或携带自由payload。

## 权威链

```text
Runtime Pack + Receipt + Authority Policy
→ 初始Runtime Session
→ NPC Intent
→ 精确授权与CAS检查
→ 冻结Runtime simulator执行或确定性拒绝
→ Adjudication Result + World Event Ledger
→ 从空Session完整重放
```

- Policy按actor精确授权`{nodeId, actionId}`；Action的`entityIds`只是交互对象，不构成权限。
- 相同Intent ID且内容完全相同会返回历史结果，不重复追加；内容不同则fail closed。
- stale revision、head或snapshot、合同损坏、身份漂移和操作故障均不返回候选Ledger。
- 合法但未授权、不可用、ending、step limit或整数溢出的Intent会形成拒绝事件，Runtime snapshot保持不变。
- 重放从全新Runtime Session开始；接受项重新执行Action，拒绝项重新计算原因，不信任存储的结果状态。
- `adjudicateNpcIntent`是无共享状态的纯裁决函数：它验证调用者提供的expected revision/head，但不提供跨进程或跨输出目录的全局原子写锁。同一旧head可以产生两个有效兄弟候选；产品宿主必须只有一个权威Ledger写者，并由其提交唯一head。

## Ledger边界

Ledger使用连续revision、previous hash、entry hash和head绑定历史顺序。它提供本地完整性与可重放证据，不提供签名、远端见证或对拥有文件完全改写权限的攻击者的来源真实性保证。需要跨设备或敌对存储防篡改时，后续必须增加独立可信head或签名机制，不能把SHA-256链本身描述为不可伪造日志。

Memory/relationship projection manifest只绑定reducer、Ledger head、entity scope和派生产物哈希。R19不实现记忆内容、embedding、关系分数、删除算法、数据库或索引。

Ledger的10,000项上限是归档、验证和重放上限，不是实时逐帧执行承诺。严格证伪证明单次完整10,000项验证与重放可完成，但连续调用当前“输入整本Ledger、返回整本Ledger”的纯函数接口会累计产生二次增长。R20必须增加单写者增量执行层；它仍需定期从初始Session完整重放，不能把缓存提升为权威状态。

## 稳定接口

- `prepareNpcAuthority`
- `createNpcAuthorityTimeline`
- `adjudicateNpcIntent`
- `replayWorldEventLedger`
- `createDerivedProjectionManifest`
- 六类严格JSON验证器

CLI只在系统临时根下以同父staging和单次rename发布新目录，不覆盖旧结果。authority来源目录和输出父目录均在读取/发布边界重复复验realpath及`dev:ino`身份。核心workspace不读文件、环境变量或网络，也不启动进程。

## 明确未实现

R19没有NPC自主行为、移动、对话、人格、长期记忆、关系算法、动态任务、世界事件生成或Godot/Creator接线。它只证明NPC Intent可被确定性、可追溯、可重放地裁决。
