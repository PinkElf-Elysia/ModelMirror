# ADR-0005：R4 Godot 独立工程底座

- 状态：Accepted
- 日期：2026-08-09

## 决策

R4 使用 Godot 4.6.3 标准版 + GDScript + Forward+，仅建立独立 Bootstrap 工程、GdUnit4 测试、headless harness 与仓外固定帧证据。R1–R3 全部权威实现冻结；不建立 Runtime Pack 桥接或玩法。

GdUnit4 v6.2.0 以精确 commit 原样 vendoring。Godot 与 MCP 工具留在仓外。MCP 只做一次性副本资格验证。

## 后果

R4 能证明 Godot 工程可复现、可拆分和可回退，但不证明游戏功能、资产管线或生产导出。下一轮可在不修改 R1–R3 语义的前提下建立 Runtime Pack → Godot 适配器。
