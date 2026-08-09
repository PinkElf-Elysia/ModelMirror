# 矩阵绿洲 AI 原生 3D 游戏引擎（实验模块）

这是模镜仓库中的独立实验模块。R3 以 R1 Authoring Game Pack/Validator 和 R2 确定性参考模拟器为冻结权威，逐批建立 Compiler、不可变 Runtime Pack、独立 Runtime Simulator 与黑盒语义等价验证；模块始终保持可独立验证、拆分和回退。

## R3 当前状态

- R3.1 只切换活动轮次、固定基线与正向范围策略，不实现 Runtime API。
- 固定基线为 `380c747e62193855c724a947d99a84070ca623ff`。
- R1 contracts、Validator/CLI、examples，R2 Simulator/语义测试以及 R0-R2 历史 ADR/验收记录字节冻结。
- schema v3 对既有 app/docs/scripts/tests 使用精确文件白名单，只对五个批准的新 R3 package 使用目录前缀。
- Creator 当前仍是 R2 最小运行实验台；到 R3.5 才会在精确白名单内演进。
- 样例仅用于测试、差分和可视化验收，不是最终成品物料。

R3 不包含 Godot、3D、AI、NPC、RAG、MCP、父项目接入、共享栈、部署或发布。

## 实施方向

```text
Authoring Game Pack 0.1.0
→ R1 Validator
→ R3 Compiler
→ Immutable Runtime Pack 0.1.0 + Receipt
→ Independent Runtime Simulator
↔ R2 Reference Simulator（black-box oracle）
→ Creator parity lab
```

R3.1 后面的组件仍须按批准批次实现和验收，文档中的方向不代表能力已经存在。R2 Simulator 只能通过包根公开 API 调用，禁止复用其内部 evaluator。

## 独立性约束

- 父项目交互为 `none`，白名单为空。
- 不依赖父仓源码、配置、环境变量、数据库、Docker、路由、资产或依赖目录。
- 模块拥有独立 manifest、lockfile、测试、诊断与拆分脚本。
- Creator 无网络访问；验证脚本只可访问 loopback。
- 所有包均为 `private`、`UNLICENSED`，不发布 npm 包。

机器规则见 [`module-boundary.json`](./module-boundary.json)，架构和范围见 [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md) 与 [`docs/BOUNDARIES.md`](./docs/BOUNDARIES.md)。

## 独立验证

在模块根执行：

```powershell
npm.cmd ci --no-audit --no-fund
npm.cmd prefix
npm.cmd ls --all
npm.cmd run check:boundary
npm.cmd run check:round-scope
npm.cmd test
npm.cmd run verify
npm.cmd run verify:extraction
```

仅在父仓 R3 worktree 中执行固定父范围保护：

```powershell
npm.cmd run check:parent-scope -- --base 380c747e62193855c724a947d99a84070ca623ff
```

`check:round-scope` 检查 committed、staged、unstaged 与 untracked 路径，冻结路径优先、未知路径失败关闭。standalone 拆分仓只在模块就是仓库根时返回 `not_applicable`。

Godot 4.6.x 仍是未来可选诊断；缺失时普通 doctor 只给出 warning，严格检查会如实失败：

```powershell
npm.cmd run doctor:godot
```

## 拆分与回退

- 使用 `git subtree split --prefix=experiments/matrix-oasis-engine` 保留历史并独立验证。
- R3 各批可逆序 `git revert`；整体回退 R3 PR 后回到完整 R2。
- 没有父路由、API、数据库、共享容器或运行数据需要恢复。
- 未经用户明确要求，不删除并行分支或 worktree。

任何父仓修改必须先填写 [`docs/PARENT_CHANGE_REQUEST_TEMPLATE.md`](./docs/PARENT_CHANGE_REQUEST_TEMPLATE.md) 并取得人工批准；任何共享栈重建必须另行确认时间窗口和共享基线。
