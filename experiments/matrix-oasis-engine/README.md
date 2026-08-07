# 矩阵绿洲 AI 原生 3D 游戏引擎（实验模块）

这是模镜仓库中的独立实验模块。R1 在 R0 隔离基线上建设案例无关的 Authoring Game Pack 合同与确定性验证器；模块仍必须可独立验证、拆分和回退。

## R1 固定范围

- R1.1 将机器策略升级为固定轮次基线，并冻结 R0 Creator 空壳。
- R1.2 提供案例无关的 Authoring Game Pack 0.1.0 权威 Schema 与只读合同导出。
- R1.3 提供确定性、无副作用的严格 JSON 验证器与模块相对路径 CLI。
- R1.4 提供题材中性的机制权威夹具、可替换的“末班地铁”薄型集成夹具和负向诊断测试。
- 样例用于验证合同与后续可视化链路，不得驱动案例专属引擎设计或叙事打磨。
- Godot 4.6.x 仍是未来可选工具，不是 R1 前置条件。

R1 不包含 Compiler、Runtime Pack、AI、NPC、RAG、MCP、Godot 工程、3D 内容、Tauri、父项目接入、部署或发布。

## 独立性约束

- 父项目交互为 `none`，白名单为空。
- 不依赖父仓源码、配置、环境变量、数据库、Docker、路由、资产或 `node_modules`。
- 模块拥有自己的 npm 根、lockfile 和验证脚本。
- Creator 源码无网络访问；验证脚本仅可访问 loopback。
- 内部实验使用 `UNLICENSED`，不发布 npm 包。

机器可读规则位于 [`module-boundary.json`](./module-boundary.json)，详细说明见 [`docs/BOUNDARIES.md`](./docs/BOUNDARIES.md)。

## 架构方向

```text
Creator
→ Authoring Game Pack
→ Validator / Compiler
→ Immutable Runtime Pack
→ Godot Runtime
```

R1 只允许定义链路前两段的作者合同与验证语义；其余组件仍只是方向，不构成接口承诺。

`apps/creator-web/**` 在 R1 中保持 R0 字节级冻结。页面中的 R0 状态是历史验收快照，不是 R1 能力状态页。

## 独立运行与验证

在模块根执行：

```powershell
npm.cmd ci
npm.cmd run dev
npm.cmd run verify:creator
npm.cmd run doctor
npm.cmd run --silent doctor -- --json
npm.cmd run check:boundary
npm.cmd run check:round-scope
npm.cmd run test:contracts
npm.cmd run test:pack
npm.cmd run validate:examples
npm.cmd run test:examples
npm.cmd run verify:pack
npm.cmd test
npm.cmd run verify
```

验证模块内单个 Pack（路径必须相对模块根，且真实目标仍位于模块内）：

```powershell
npm.cmd run --silent validate:pack -- examples/mechanics-conformance.authoring-game-pack.json --json
```

CLI 对合法内容返回 0、内容无效返回 1、工具或路径错误返回 2。`--json` 模式只向 stdout 输出一行稳定报告。两个示例都只是合同、诊断与未来只读可视化的验收夹具，不是产品剧情或最终物料。

其中带 `--silent` 的 doctor 命令是机器可解析入口：其标准输出只包含一个 JSON 文档，不带 npm 生命周期前缀。普通 `npm.cmd run doctor` 保留为供人工阅读的诊断输出。

缺少 Godot 不会阻塞 R1，普通 doctor 返回 `ready_with_warnings`。用于后续轮次的严格检查会如实失败：

```powershell
npm.cmd run doctor:godot
```

历史保留型拆分演练：

```powershell
npm.cmd run verify:extraction
```

仅在父仓 worktree 中执行固定基线范围保护：

```powershell
npm.cmd run check:parent-scope -- --base 8deeebb85d2db1b7f1b3564fca984503ce5787a2
```

`check:parent-scope` 拒绝模块外变更；`check:round-scope` 还会拒绝冻结的 Creator 变更。两者使用代码与策略共同固定的 R1 基线，不能通过传入较新提交缩短范围。拆分后的 standalone 仓对 round scope 明确返回 `not_applicable`，不会伪装成父仓检查已通过。

该命令只从干净 worktree 克隆当前 HEAD，在一次性临时目录执行 `git subtree split`，并在拆分仓库根从空依赖完成安装、完整验证与 source-only archive 哈希。成功后只清理自身创建的临时目录；失败时保留并报告精确诊断目录。

## 拆分与回退

- 历史保留型拆分使用 `git subtree split --prefix=experiments/matrix-oasis-engine`，并在一次性临时仓库中验证。
- 模块没有父路由、API、数据库、环境变量、Docker 服务或运行数据。
- PR 前可逆序 `git revert` R1 模块专属提交。
- 合并后可单独 revert R1 PR，不需要回退 R0；现有 `/matrix-oasis` 占位页保持不变。
- 未经用户明确要求，不删除 R0/R1 分支或 worktree。

任何需要修改父仓文件的提案必须先填写 [`docs/PARENT_CHANGE_REQUEST_TEMPLATE.md`](./docs/PARENT_CHANGE_REQUEST_TEMPLATE.md) 并经用户人工批准。
