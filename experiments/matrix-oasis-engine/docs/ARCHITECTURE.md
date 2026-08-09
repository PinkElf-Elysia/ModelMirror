# 架构方向

最后更新：2026-08-08
状态：R3.5 Creator 双执行锁步实验台

## 当前系统

R1 Authoring Game Pack 0.1.0 与 Validator、R2 确定性参考模拟器保持冻结。R3.5 已新增 canonical-json/1、确定性 Compiler、Runtime Pack/Receipt 0.1.0 合同、严格 Validator、模块内安全 CLI、独立 Runtime Simulator、包根黑盒 parity harness 与 Creator 锁步实验台。Creator 只依赖 parity harness 公共入口，不自行实现或读取两套 evaluator。

## R3 目标数据流

```text
Authoring Game Pack 0.1.0
  → R1 Validator（冻结）
  → R3 Compiler
  → Immutable Runtime Pack 0.1.0 + Receipt
  → Independent Runtime Simulator
  ↔ R2 Reference Simulator（冻结、仅包根黑盒调用）
  → Creator parity lab
```

R3 不把 Runtime Pack 提前等同于 Godot 格式，也不复用 R2 evaluator。两个 Simulator 独立实现；parity harness 只比较 source identity 投影、位置/正文/实体、变量、action 可用性、步数、transition 与 Cue，不以共享代码自证等价。

Creator 将一次会话视为同一原子 bundle：source、opaque parity prepared handle、规范 Artifact、双侧 snapshot、公共 inspection、Cue 与最近 transition。异步本地候选、重置和单步都基于捕获的当前 bundle 计算，并在提交时以引用 CAS 再核对；迟到结果、验证失败、运行失败或 parity mismatch 均保留当前会话。Pack 与 Receipt 只在用户明确点击后通过浏览器内存下载，不写 storage，也不接网络。

## 独立模块原则

- 模块内部拥有 manifest、lockfile、构建、测试、诊断和拆分脚本。
- 父仓能力仍只是未经接入的“可复用工具箱”；没有批准的适配器前，父项目交互为空。
- 运行依赖只能指向模块内 workspace 公共入口，禁止读取父源码、父环境或模块外路径。
- 新格式与新执行器保持浏览器兼容、无网络、无持久化，并可随模块 subtree 拆分。
- 每批都先通过固定范围、自动验证与回退门，再进入下一批。

## 决策记录

- [ADR-0001：独立实验模块](./adr/0001-isolated-experiment-module.md)
- [ADR-0002：R1 活动轮次治理](./adr/0002-r1-active-round-governance.md)
- [ADR-0003：R2 参考模拟器治理](./adr/0003-r2-reference-simulator-governance.md)
- [ADR-0004：R3 Runtime Pack 与双执行治理](./adr/0004-r3-runtime-pack-governance.md)
