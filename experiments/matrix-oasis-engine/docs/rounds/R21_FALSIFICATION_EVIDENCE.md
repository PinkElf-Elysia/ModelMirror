# R21 针对性证伪证据

状态：针对合同、投影、事务发布、真实缓存和复杂度假设的攻击均通过；详细原始输出仅保存在仓外。

## 被主动推翻的假设

1. **合法JSON即可成为可信persona或关系策略**：伪造actor集合、越权actor、未授权Action、不存在target、自关系、persona/policy权威换身均被拒绝。
2. **R19 manifest足以证明artifact正确**：用合法manifest包装伪造memory/relationship、替换reducer hash、扩大scope或更换源身份均在R21完整重算时失败。
3. **重新计算Ledger哈希即可隐藏删改**：删除、插入、重排、transition/head/timeline篡改会失败；即使攻击者重算剩余哈希链，也无法匹配原Bundle绑定的Ledger身份。
4. **cue可以偷偷成为指令或关系来源**：包含JSON、控制字符和伪造ID的cue不会进入memory，也不会改变interaction provenance或关系边。
5. **删除重建可能复用旧字节**：资格流程先做20次一致性生成，再把自建探针目录从成功命名空间原子隔离，随后从同一只读Ledger重新生成；全部canonical字节和content ID一致。
6. **历史垃圾可以替代或阻断current**：真实缓存暴露R20根内存在无关历史未完成时间线。R21现仅审计完整资格链并精确绑定`npc-current`；历史未完成目录不参与候选，current损坏、资格分叉和pending后继仍失败关闭。

## 自动证据

- R21合同、runtime、CLI、资格、投影、证伪与参考测试合计：63通过、0失败、1个真实缓存opt-in测试在普通离线门中按设计跳过。`verify:r21`在补入10,000 accepted事件的公开API容量测试后重新通过；重复执行的合同测试不重复计数。
- 同一完整输入20次：persona、memory、relationship、两个manifest、replay report和Bundle字节一致。
- 10,000条Ledger：单次reducer扫描、按规则索引，复杂度计数符合`O(entries + rules + contributions)`。此前约45 ms的数字只代表reducer，不是完整重放或资格CLI耗时。
- 新容量夹具使用冻结Runtime生成10,000条accepted事件，再由R21公开API独立执行R19完整重放、投影与再次验证；两次本机测试总耗时约24.3秒和16.7秒，均低于60秒。两个actor各5,000条memory，三条关系规则各贡献一次，最终ending snapshot hash与原Runtime一致。此数字不包含资格CLI的20次重复、文件发布或删除重建，不将其冒充完整资格CLI计时。
- 事务攻击覆盖：rename前/后故障、竞争目录、父目录换身、未知文件注入、open/fsync失败、源head变化、lease释放失败和输出移植。
- R1–R20产品路径无生产代码改动；R18/R20两项历史状态测试只移除“当前轮次必须永久等于R20”的陈旧断言，继续强制`claimAllowed=false / blockingRound=R25`，避免未来每轮再次破坏历史回归。

## 两份真实资格

| 案例 | Ledger | Memory | Bundle SHA-256 | 资格报告文件 SHA-256 | R20源树SHA-256 |
|---|---:|---:|---|---|---|
| 中性缓存 | 2 accepted | 2 episodes | `7cfc9b43e6318076b57ee97e34a759a9b9a52c31377a9beaaf5d8d829d1a008b` | `9280f1a1caff4ee926708be0aadb2a1c434b26ee2c8a3282eab8e76727157ed2` | `74b33027d099554b1c51ffed603c38b79a9ec66ef0072b4a98fdd5ab097b61ff` |
| 末班地铁缓存 | 6 accepted | 6 episodes | `4feb459d9e64b66a45403bd66aaae7d6056d3ca859696e4afb0f867085a295e7` | `481d8b10cbcf625436fa5482ff132f22da81afadc214e4ddeebbf4a2653f713e` | `c588df0fcf31f519d2dbe5b9d36cd8cd9258a6414328764b229917618ef98bc5` |

两份真实资格均使用机械生成的零值Persona Seed和空Relationship Policy，因此只证明真实身份链、actor-self memory、删除重建和Bundle绑定；非零定向关系、拒绝零贡献、循环重复和双actor贡献由通用合成fixture证明。两次源树在资格前后哈希完全一致。

## 证据边界

外部模型、embedding、数据库、供应商请求、凭据读取和费用均为0。隐藏quarantine保留原验证字节，因此“删除”不等于安全擦除。R21也未证明语义检索、选择性遗忘、跨timeline长期记忆、AI人格或关系驱动行为。
