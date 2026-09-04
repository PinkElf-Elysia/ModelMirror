# R21 单时间线派生状态验收记录

状态：R21核心、针对性证伪、双真实缓存资格及最终全量回归已通过；第七提交后仍须由clean-HEAD extraction取得独立拆分回执，随后等待用户人工验收，并等待用户决定是否允许push和创建PR。

## 边界与结论

- 基线：`cbb50f1095a51f2c32958ab4f7dd4e34dadfc2c2`
- 分支：`codex/matrix-oasis-r21-derived-state`
- profile：`matrix-oasis.npc-derived-state/1`
- R16 Creator默认入口、R19权威合同、R20调度和Godot桥、供应商适配器均未修改。
- `docs/V2_STATUS.json`更新为`r21-derived-state-qualified`，但第二版声明继续为`claimAllowed=false`并阻断至R25。

R21只证明：可信结构化Persona Seed、actor-self accepted Action记忆及显式定向整数关系投影，可由单timeline Ledger确定性生成、整体删除并字节级重建。它不宣称AI人格、语义记忆、选择性遗忘、跨timeline记忆、对话、关系驱动行为、动态任务或世界事件完成。

## 七批交付

| 批次 | 本地提交 | 结果 |
|---|---|---|
| R21.1 治理与来源二次核查 | `8f87d950` | 完成 |
| R21.2 派生状态合同 | `36d83d22` | 完成 |
| R21.3 确定性投影 | `3c3b1233` | 完成 |
| R21.4 事务CLI | `35fbc7ad` | 完成 |
| R21.5 针对性证伪 | `a9e1ed56` | 完成 |
| R21.6 双真实缓存资格 | `241a59ca` | 完成 |
| R21.7 文档、状态与最终门 | 本批提交 | 完成后等待用户PR授权 |

## 人工检查映射

1. 合成双actor fixture证明：accepted Action生成actor自身memory和一次显式关系贡献；rejected与循环重复贡献为0。
2. 资格Harness证明：成功命名空间中的全部派生产物被移除后，第二次重建的所有字节和content ID完全一致。
3. artifact、manifest、reducer或Ledger篡改均fail closed，R20源时间线树哈希不变。
4. 中性与末班地铁两份R20 current均离线生成并验证Bundle，分别得到2和6条memory episode。
5. R20预览、R16 Creator和R16 MVP状态保持原行为；网络请求、凭据读取和供应商费用为0。

这些检查由可重复的CLI和自动fixture提供证据，不冒充用户已进行独立源码审阅。R21没有新增用户界面或Godot体验，因此最终人工门是对上述证据、语义边界和限制是否可接受的确认。

## 已知限制与回退

- Windows删除证明是受控命名空间隔离，不是安全字节擦除；隐藏quarantine仍持有原字节。
- 真实案例使用零值persona与空关系策略，非零关系语义由合成fixture证明。
- 仓外资格产物不会由Git回退删除。
- 停用R21 profile并逆序revert七个提交即可回到R20，Runtime、Ledger与Creator不受影响。

## 最终检查记录

- `verify:r21`：通过；63项唯一自动测试通过，普通离线门中的真实缓存opt-in用例按设计跳过，双真实缓存证据独立记录。
- 干净父`client`（同一R21基线）：`npm ci`、`npm run test:run`、`npm run build`通过；130个Vitest文件、881项测试，以及1项Node header测试通过。只有既有chunk体积提示和锁文件依赖审计告警，没有修改依赖来消除提示。
- `check:parent-scope -- --base cbb50f1095a51f2c32958ab4f7dd4e34dadfc2c2`与`git diff --check`：通过，父`client`源码零差异。
- 先前长`verify`会话以进程级中断结束，未将其当作整套成功；沙箱内重跑又因历史R17夹具无法在`C:\\tmp`创建目录而出现同源`EPERM`，也未当作代码失败或成功。随后在批准的本地验证权限下执行完全相同的`npm.cmd run verify`，取得`VERIFY_OK steps=30`及明确退出码0；其中聚合`npm test`为935通过、0失败。clean-HEAD extraction未获得成功退出记录前，仍不宣称R21最终收口。

第六提交的clean source为`241a59ca0809b4a9b10b41aced18a895bc1ac416`。R21.7最终clean HEAD的source/split/archive身份将在本批提交后的standalone extraction中生成，并在PR交付摘要记录，避免文档自引用改变已记录source身份。
