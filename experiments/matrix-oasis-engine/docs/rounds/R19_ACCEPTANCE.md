# R19 NPC权威合同验收记录

状态：实现、针对性证伪、全量回归和中性CLI验收均完成；待clean提交复验与人工验收

## 固定基线

- `R19_BASE_SHA=821067a7db4811a3f3f1fd649e4fdfade9eafb22`
- 分支：`codex/matrix-oasis-r19-npc-authority-contracts`
- 版本：`0.19.0-r19`
- 供应商、外部模型、Docker和共享栈请求：0

## 六批交付

1. `01d6cf03` `chore: 建立矩阵绿洲 R19 NPC权威边界`
2. `264dfcd6` `feature: 定义矩阵绿洲 NPC意图与事件合同`
3. `7c4962c2` `feature: 实现矩阵绿洲 世界事件Ledger`
4. `a2a28c2b` `feature: 实现矩阵绿洲 NPC确定性裁决`
5. `6723243e` `test: 证伪矩阵绿洲 NPC裁决与Ledger重放`
6. `docs: 记录矩阵绿洲 R19 权威合同证据`

## 已证明

- Policy、Intent、Result、Ledger、Projection Manifest和Replay Report均为闭合canonical合同。
- Runtime是唯一状态转换权威；Action `entityIds`不能提升actor权限。
- 精确重复Intent幂等返回，同ID异内容、陈旧revision/head/snapshot及身份漂移均fail closed。
- 接受和拒绝混合历史、循环与ending可从空Session重建；拒绝和整数溢出不改变snapshot。
- entry删除、插入、重排、decision/head篡改及重算哈希链后的transition伪造均被合同或Runtime重放检出。
- CLI拒绝覆盖、路径越界、junction、输入换身和并发双写；失败不留下可发布半成品。
- 同一输入20次的Result、Ledger、Projection Manifest和Replay Report字节一致。

## PR前严格证伪增量

- 证伪并修复authority来源目录TOCTOU：旧实现只验证目录一次；现在五个输入读完后复验同一`dev:ino`和realpath，换身返回`NPC_AUTHORITY_CLI_INPUT_CHANGED`且零发布。
- 证伪并修复输出父目录TOCTOU：现在staging前、rename前和rename后均复验父目录身份；换身不会留下目标或staging残留。
- 为projection artifact新增16 MiB硬上限，在复制/哈希前拒绝；外部manifest的`byteLength`也受同一合同限制。
- 完全重算哈希链后的错误transition与错误拒绝原因均被Runtime重放检出；1,536个固定种子畸形JSON变体未逃逸为原始解析异常。
- 真实构造10,000条合法拒绝事件：合同验证、完整重放、精确重复及容量拒绝全部通过；第10,001条不返回候选Ledger。
- 证伪确认expected-head只提供单次纯函数CAS，不提供全局写者：同一旧head可产生两个兄弟候选，使用其中一个新head后另一个旧Intent稳定返回stale。R20必须实现单一权威写者。
- 当前机器上128次连续全量裁决约3.2–5.2秒；单次10,000项合同验证与重放低于30秒门。前者确认全量JSON接口存在累计二次增长，R20必须采用增量执行缓存并保留全量重建门。

稳定标识：

```text
R19_ADJUDICATION_FAIL_CLOSED
R19_LEDGER_REBUILD_DETERMINISTIC
R19_CONTRACTS_CANONICAL
```

## 已知边界

- SHA-256链证明本地内容和顺序一致，不提供签名或外部可信head；拥有全部文件改写权限的攻击者可重写并重算整条链。R19不宣称来源真实性。
- R19没有实现NPC行为、移动、对话、人格、记忆内容、关系算法、任务或动态事件，也没有修改Creator和Godot。
- Projection Manifest只是派生产物身份清单，不是记忆或关系实现。
- Projection Manifest的`scopeEntityIds`在R19只是有类型的身份范围，尚不根据Runtime实体目录做外部引用解析；R21消费前必须与绑定Runtime重新核对。
- R19 CLI输出是不可覆盖的候选目录，不是全局唯一head存储。并发时间线提交和持久化串行化属于R20宿主硬门。
- R16 MVP声明保持不变；经用户精确批准，只迁移共享V2状态及R18状态断言，R17/R18来源哈希和选型证据继续字节冻结。当前为`r19-authority-qualified`、`claimAllowed=false`，继续阻断至R25。

## 最终验证

预提交结果：

- `npm.cmd ci`：通过；锁文件未改。npm审计报告2项既有依赖告警（1 low、1 high），另有`esbuild`与`webgpu`生命周期脚本审批提示；R19未修改依赖或自动执行`npm audit fix`。
- `npm.cmd prefix`：精确指向独立模块根；`npm.cmd ls --all`：退出0，无missing/extraneous问题。
- `npm.cmd run verify:r19-references`：`R19_REFERENCES_OK references=5`。
- `npm.cmd run verify:r19-contracts`：14/14通过。
- `npm.cmd run verify:r19`：54/54通过，并输出三个稳定退出标识；其中10,000项完整Ledger重放约6.4秒，128次连续裁决约3.0秒。
- `npm.cmd run verify`：28个阶段全部通过；主测试930/930，Creator build与smoke通过。
- `npm.cmd run check:round-scope`：`ROUND_SCOPE_OK checked=58 changed=46`。
- `npm.cmd run check:parent-scope -- --base 821067a7db4811a3f3f1fd649e4fdfade9eafb22`：`PARENT_SCOPE_OK checked=58 changed=46`。
- `git diff --check`：通过。
- 父`client`在同一隔离worktree执行`npm.cmd ci`、`typecheck`、`test:run`和`build`：typecheck通过，122个测试文件/746项测试通过，生产build通过。npm审计报告5项既有依赖告警（1 low、2 moderate、2 high）；R19未修改父client或其锁文件。

`verify:r19`的CLI夹具在受限沙箱内曾返回`EPERM`，允许计划内`C:\tmp`测试写入后54/54通过。证伪回归中，未注入`GODOT_BIN`的主测试为929/930，唯一失败的doctor用锁定Godot 4.6.3复跑为1/1；受限沙箱中的R14 CLI和Creator清理也分别因`C:\tmp`与`dist`写权限返回`EPERM`，相同命令在计划允许的测试权限下均通过。最终带锁定Godot且允许仓外夹具的`npm.cmd run verify`原样通过28个阶段、930/930主测试、Creator build与smoke，完整日志只保存在`C:\tmp`。`verify:extraction`明确要求clean HEAD，因此当前未提交证伪补丁不具备该门的有效前置条件；取得提交授权并形成clean源树后必须原样重跑，并将source/split/tree/archive哈希保存在仓外交付清单。

## 中性CLI验收证据

合格目录：`C:\tmp\matrix-oasis-r19-neutral-cli-evidence-20260828-v4`

- 两个actor按精确grant执行；一次未授权Intent被拒绝且Runtime snapshot字节身份不变。
- 随后执行一次可达循环并进入ending；Ledger共5项，其中4项接受、1项拒绝。
- 从空Session重放5项得到同一final snapshot：`sha256:61afb4922dcaf78add09aecdd326111ba0e0d9db1e5fcc0445e5b5b0c1bc55cb`。
- Ledger：`sha256:1bc90b5f057eb4783cdd1deaaf3b0f4d07d975737ebe3950708599ecfece5f6d`。
- Replay Report：`sha256:59c56c92e93621e6fe6e0c8b71486c463e33192d2ef9f6c2d51760e92ec735f8`。
- 篡改head后重放返回静态`WORLD_EVENT_LEDGER_HEAD_MISMATCH`且不发布目标。
- Memory manifest：`sha256:407cab5f1ecb69c04daa00c4ce60ee369d2256e87cd70c9cc0bb9d8b122fdb66`。
- Relationship manifest：`sha256:09269d851ad91c6355b30add7321d74961716dc778be6410693a16d1c777b352`。
- 两份manifest均只含reducer、Ledger、scope和artifact身份，不含投影内容。

前三个仓外演练目录因验收驱动的节点顺序、CLI exit分类或artifact format夹具预期错误而作废；这些失败未触发产品代码改动，也不计为合格证据。只有clean extraction通过且用户人工确认后才允许push或创建PR。
