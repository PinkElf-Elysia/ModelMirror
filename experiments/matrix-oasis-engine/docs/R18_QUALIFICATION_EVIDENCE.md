# R18 分类隔离资格证据

## 结论

R18.6在零外部模型、零供应商调用、零容器和零依赖安装的边界内，实际执行了13个唯一候选。证据集合只证明本轮固定fixture下的结果，不代表候选已进入产品，也不建立生产推荐。

| 状态 | 候选 | 结论边界 |
|---|---|---|
| `executed` | Creator Qualification内部基线、原生Control对话基线、Runtime Evidence内部基线 | 固定本地测试通过；文件系统隔离仍只达到观测级证据 |
| `evidence-gap` | Deterministic Runtime内部基线、World Event Ledger内部基线、静态角色资产基线 | 分别缺少规划/行为桥、Ledger合同和动画能力 |
| `evidence-gap` | Beehave | Godot 4.6.3受控退出通过，但尚未执行2/4/32/64 Agent负载与20次语义trace |
| `evidence-gap` | Kenney Animated Characters Retro | Godot导入与300帧运行通过，但固定资产缺少独立turn clip |
| `evidence-gap` | LimboAI | 精确源码身份通过；未加载来源闭包无法证明的预编译GDExtension |
| `evidence-gap` | Mem0 | loopback SDK CRUD、更正、删除、导出和隔离执行20次；OSS Memory路径受未批准的原生依赖阻断，不能把SDK传输层结果冒充记忆引擎资格 |
| `evidence-gap` | Concordia、TinyTroupe | 固定归档和许可证通过；Git checkout身份未证明。Concordia只完成本地import探针，TinyTroupe依赖面未闭合 |
| `evidence-gap / unresolved` | Dialogue Manager | 修复Harness资源加载方式后完成20次语义trace并正常退出，但Godot日志仍不干净；不得归因为候选失败，也不得升级为通过 |

## 证据身份

- 最终集合只保存在仓外资格目录；仓库不记录机器绝对路径。
- 脱敏证据集SHA-256：`b7d78a166fcfc86a2084055434beac58bf362e2afbd01cb3bc780eac87a803a3`。
- 仓库只保存canonical状态、诊断、fixture trace哈希和报告哈希；候选源码、依赖、二进制、资产和原始运行输出均未提交。
- Concordia与TinyTroupe明确记录为`archive-only`，没有用Git树重建近似值冒充锁定checkout。

## 决策约束

- `executed`、`evidence-gap`和`failed`都计入“实际执行候选”配额，但只有硬门通过才能成为正式集成推荐；本轮没有候选证明完整进程树残留隔离。
- Harness或归因未决的失败保持`deferred`，不得写成`rejected`。
- 桌面硬门通过的候选可以继续保留在可执行短名单；资格缺口必须进入切换条件和后续进入门。
- R18.7形成最终矩阵前，不产生任何`integration-recommended`结论。
