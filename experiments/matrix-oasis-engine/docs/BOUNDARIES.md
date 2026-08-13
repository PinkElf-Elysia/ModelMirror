# R11边界

## 允许

- 两个新私有workspace、明确R11 CLI/测试/文档；
- `apps/runtime-godot/spatial_prototype/**`新wrapper；
- 原样vendored `apps/runtime-godot/addons/gdgs/**`与精确供应链锁；
- 明确列出的Creator package、App、styles和prototype-builder文件；
- `@playcanvas/splat-transform@3.3.0`与`@adobe/spz@0.2.2`离线转换；
- 仓外`C:\tmp`输入、转换缓存、run、截图与性能证据。

## 冻结

R1–R10 contracts、validator、compiler、simulator、Blueprint、Asset/Environment Bundle、Scene Pack、examples、既有Godot、历史vendor、历史ADR/验收和Creator旧模式均冻结。精确allowlist优先于冻结根，未知路径fail-closed。

schema v11固定`activeRound=R11`与基线`da2a914a2ff131507750a0afb8d8881180530f62`。所有父仓路径、Docker、共享栈、父服务、数据库和父凭据存储禁止修改或依赖；普通验证禁止供应商网络调用。
