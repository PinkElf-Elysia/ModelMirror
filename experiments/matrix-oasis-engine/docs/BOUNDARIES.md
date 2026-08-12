# R10边界

## 允许

- 两个新私有workspace、明确R10 CLI/宿主/测试/文档；
- `apps/runtime-godot/prototype_builder/**`新wrapper；
- 明确列出的Creator package、App、styles和prototype-builder文件；
- Marble adapter访问固定API与受控官方资产host；Creator/Godot仍无外网；
- 仓外`C:\tmp`缓存、staging、run和资格证据。

## 冻结

R1–R9 contracts、validator、compiler、simulator、Blueprint、Asset Bundle、Scene Pack、examples、既有Godot、vendor、历史ADR/验收和Creator旧模式均冻结。精确allowlist优先于冻结根，未知路径fail-closed。

schema v10固定`activeRound=R10`与基线`09f4cca4f1e02fe275ada17535597437cac3778d`。所有父仓路径、Docker、共享栈、父服务、数据库和父凭据存储禁止修改或依赖。
