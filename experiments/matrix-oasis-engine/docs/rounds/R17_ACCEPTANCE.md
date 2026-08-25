# R17第二版开源资格验收记录

状态：证伪攻击后的Harness修复与仓外重新取证完成；V2仍不可声明，修复结论待用户人工确认

## 固定基线

- `R17_BASE_SHA=66b57c3c83277bea960464decc2d4e46965a5ef1`
- 分支：`codex/matrix-oasis-r17-v2-qualification`
- 版本：`0.17.0-r17`
- 仓外旧证据（已失效）：`C:\tmp\matrix-oasis-r17-evidence-final-v2-20260824`
- 仓外修复后证据：`C:\tmp\matrix-oasis-r17-evidence-remediated-20260825`
- 供应商请求：0；Docker/容器：未使用；候选源码、二进制、日志和资格输出：未提交

## 六批交付

1. `69785429` `chore: 建立矩阵绿洲 R17 第二版选型边界`
2. `76068d63` `chore: 固定矩阵绿洲 第二版候选来源`
3. `1c0f9d62` `test: 添加矩阵绿洲 第二版资格验证框架`
4. `645a5f7d` `test: 资格验证矩阵绿洲 Godot NPC候选`
5. `3fdb89ff` `test: 资格验证矩阵绿洲 Agent与记忆候选`
6. `docs: 记录矩阵绿洲 R17 第二版选型结论`

## 资格结论

- 行为树：现有Runtime状态机继续作为内部基线。LimboAI实际运行包虽然20次trace一致，但CC-BY-4.0许可面与GDExtension来源硬门失败，`rejected`；Beehave的Godot/GdUnit执行面未证成，`deferred`。
- 对话：现有原生Control继续推荐；Dialogue Manager虽完成20次一致trace，但仅为Compatibility渲染且有资源泄漏，`deferred`。
- 记忆：权威源继续是未来World Event Ledger；Mem0仅证明SDK对测试自建API的传输，依赖树不完整且没有验证真实记忆实现，`deferred`；Letta仍为`deferred`。
- 动画：Kenney固定包版本与页面不一致且缺walk/turn；KayKit无固定归档哈希，均`deferred`，不作为R18前置。
- WorldX只提供阶段化产品编排参考；Concordia行动裁决、AI Town事务边界和R16权威Runtime共同约束R18，不建立第二套世界状态。

机器可读来源、分数、硬门、切换条件与证据SHA-256见`docs/R17_QUALIFICATION_SUMMARY.json`；人工可读采用/备选/拒绝依据见`docs/R17_V2_SELECTION_MATRIX.md`。

## 验证证据

- `npm.cmd ci`、`npm.cmd prefix`、`npm.cmd ls --all`：通过；仅显示非当前平台可选依赖未安装。
- `npm.cmd run doctor:godot`：Godot `4.6.3`通过。
- `npm.cmd run verify:r17-evidence -- --evidence-root C:\tmp\matrix-oasis-r17-evidence-remediated-20260825`：5份报告及其raw artifact派生重算通过；旧证据因缺少报告声明的raw artifact已被新验证器拒绝。
- `npm.cmd run verify:r17`：6阶段通过。
- `npm.cmd run verify`：27阶段通过；综合测试920/920，Creator build与smoke通过。stdout日志SHA-256为`87d0b5f97eeacb67aadcbf29c42394bf475f131cd2c7a57d62d05b8e1c772ca4`；stderr为0字节，SHA-256为`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`。日志仅在`C:\tmp`。
- `npm.cmd run check:round-scope`：`checked=77 changed=72`。
- `npm.cmd run check:parent-scope -- --base 66b57c3c83277bea960464decc2d4e46965a5ef1`：`checked=77 changed=72`。
- 父`client`：clean `npm.cmd ci`、`npm.cmd run test:run`（108个文件、625/625测试）与`npm.cmd run build`通过；build仅报告既有大chunk告警，安装审计仍报告父仓既有5项漏洞，均未在R17扩张范围内修复。仓外辅助脚本在写结果JSON时因Windows PowerShell不支持`utf8NoBOM`枚举而报错，发生在测试和build均成功完成之后，不影响命令输出证据。
- `git diff --check`：通过。
- `npm.cmd run verify:extraction`：在人工作出选型确认并形成第六提交后，以显式锁定的仓外Godot 4.6.3对clean源码树执行通过；最终source/split/tree/archive身份由仓外交付清单记录。

## 已知边界

- R17没有正式引入LimboAI、Dialogue Manager、Mem0或任何动画资产，也没有修改Creator和Godot产品路径。
- 当前没有外部候选达到`recommended`或`backup`；这是一项有效选型结论，不得通过README功能声明、一次成功运行或分数覆盖硬门。
- 修复后Harness可清理超时进程树并验证raw artifact闭包，但非容器Windows进程不能证明文件系统和网络隔离；相关硬门保持`not-proven`。
- 证伪修复已纳入第六提交；standalone extraction只验证clean `HEAD`，没有使用`--allow-dirty`或旧提交冒充修复版证据。
- Context7当前月度额度不足；本轮以固定上游commit、许可证、manifest/lockfile和真实Godot/loopback执行证据完成资格，未把不可获取的二手文档当作运行证据。
- `docs/MVP_STATUS.json`保持R16完成；`docs/V2_STATUS.json`为`r17-selection-qualified`、`claimAllowed=false`、`blockingRound=R24`。
- R18只能定义NPC意图、事件账本、裁决结果、派生记忆和观察投影合同；不得提前开发AI人格、长期关系、动态任务、模型驱动事件或运行期供应商调用。
