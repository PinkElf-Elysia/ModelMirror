# 架构方向

最后更新：2026-08-08
状态：R3.1 治理与隔离基线

## 当前系统

R1 Authoring Game Pack 0.1.0 与 Validator、R2 确定性参考模拟器及 Creator 最小运行实验台已经存在。R3.1 只冻结这些权威输入并建立 R3 范围门；Compiler、Runtime Pack、Receipt、独立 Runtime Simulator 与 parity harness 尚未实现。

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

R3 不把 Runtime Pack 提前等同于 Godot 格式，也不复用 R2 evaluator。两个 Simulator 必须独立实现，并只通过公开结果投影做差分。

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
