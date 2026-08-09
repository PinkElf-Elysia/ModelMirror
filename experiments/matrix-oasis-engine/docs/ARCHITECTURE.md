# 架构方向

最后更新：2026-08-09
状态：R4 Godot 独立工程底座

## 当前系统

R1–R3 的合同、Validator、Compiler、两套 Simulator、parity harness 与 Creator 均为冻结权威。R4 不修改或接入这些包，只新增独立 Godot 4.6.3 工程和验证 harness。

```text
Authoring / Runtime Pack / parity（R1–R3，冻结）
                         │ R4 不接线
                         ▼
Godot 4.6.3 project → headless import → GdUnit4 → smoke → fixed-frame evidence
```

R4 Bootstrap 仅由 Godot 内建 Node3D、MeshInstance3D、Camera3D、DirectionalLight3D 与 Environment 构成，不包含游戏状态、输入、资产导入或运行包解释器。

## 独立模块原则

- Godot 二进制、MCP 工具、图形证据和详细日志在仓外。
- 正式源码只依赖 Godot 标准 API；GdUnit4 是 dev-only vendored 测试框架。
- 自动门只使用 headless；图形捕获是 PR 前人工硬门。
- 下一轮才能定义 Runtime Pack → Godot 的适配边界。

## 决策记录

- [ADR-0001：独立实验模块](./adr/0001-isolated-experiment-module.md)
- [ADR-0002：R1 活动轮次治理](./adr/0002-r1-active-round-governance.md)
- [ADR-0003：R2 参考模拟器治理](./adr/0003-r2-reference-simulator-governance.md)
- [ADR-0004：R3 Runtime Pack 与双执行治理](./adr/0004-r3-runtime-pack-governance.md)
- [ADR-0005：R4 Godot 工程底座治理](./adr/0005-r4-godot-foundation-governance.md)
