# 矩阵绿洲 AI 原生 3D 游戏引擎（实验模块）

这是模镜仓库中的独立实验模块。R2 以已验收的 Authoring Game Pack 合同与确定性验证器为冻结输入，建设案例无关的确定性参考模拟器与 Creator 最小运行实验台；模块仍必须可独立验证、拆分和回退。

## R2 固定范围

- R2.1 将活动轮次切换到固定 R2 基线，并以正向 allowlist 限制本轮变更。
- R1 contracts、validator、examples 与 R0/R1 验收记录保持字节冻结。
- R2.2 已在 `packages/game-pack-simulator/**` 建立浏览器兼容的确定性参考模拟器。
- R2.3 以中性权威夹具固定完整语义矩阵，并仅把“末班地铁”作为可替换集成输入。
- R2.4 才会把 Creator 演进为最小运行实验台；当前页面仍是 R0 空壳。
- 样例仅用于测试、语义追踪和可视化验收，不得驱动题材专属引擎设计或叙事打磨。
- 模拟器严格遵循已批准的公开接口与语义；不宣称 Creator 已具备尚未实现的运行能力。
- Godot 4.6.x 仍是未来可选工具，不是 R2 前置条件。

R2 不包含 Compiler、Runtime Pack、生产运行时、AI、NPC、RAG、MCP、Godot 工程、3D 内容、Tauri、父项目接入、部署或发布。

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
→ Validator
→ Deterministic Reference Simulator
→ Compiler（未来）
→ Immutable Runtime Pack
→ Godot Runtime
```

R1 已完成作者合同与验证语义。R2 只允许在冻结合同之上建设可复现、可观察的参考执行语义；Compiler、Runtime Pack 与 Godot Runtime 仍只是未来方向。

R2.3 尚未修改 `apps/creator-web/**`；R2.4 可将其从 R0 空壳演进为最小运行实验台，但不能连接父项目、网络或持久化能力。

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
npm.cmd run verify:simulator
npm.cmd test
npm.cmd run verify
```

验证模块内单个 Pack（路径必须相对模块根，且真实目标仍位于模块内）：

```powershell
npm.cmd run --silent validate:pack -- examples/mechanics-conformance.authoring-game-pack.json --json
```

CLI 对合法内容返回 0、内容无效返回 1、工具或路径错误返回 2。`--json` 模式只向 stdout 输出一行稳定报告。两个示例都只是合同、诊断与未来只读可视化的验收夹具，不是产品剧情或最终物料。

其中带 `--silent` 的 doctor 命令是机器可解析入口：其标准输出只包含一个 JSON 文档，不带 npm 生命周期前缀。普通 `npm.cmd run doctor` 保留为供人工阅读的诊断输出。

缺少 Godot 不会阻塞 R2，普通 doctor 返回 `ready_with_warnings`。用于后续轮次的严格检查会如实失败：

```powershell
npm.cmd run doctor:godot
```

历史保留型拆分演练：

```powershell
npm.cmd run verify:extraction
```

仅在父仓 worktree 中执行固定基线范围保护：

```powershell
npm.cmd run check:parent-scope -- --base a8e627e217c8c9e2cb8cca83fea8542c47edaeba
```

`check:parent-scope` 拒绝模块外变更；`check:round-scope` 按 R2 正向 allowlist 放行，并优先拒绝 R1 核心与历史验收记录变更。两者使用代码与策略共同固定的 R2 基线，不能通过传入较新提交缩短范围。拆分后的 standalone 仓对 round scope 明确返回 `not_applicable`，不会伪装成父仓检查已通过。

该命令只从干净 worktree 克隆当前 HEAD，在一次性临时目录执行 `git subtree split`，并在拆分仓库根从空依赖完成安装、完整验证与 source-only archive 哈希。成功后只清理自身创建的临时目录；失败时保留并报告精确诊断目录。

## 拆分与回退

- 历史保留型拆分使用 `git subtree split --prefix=experiments/matrix-oasis-engine`，并在一次性临时仓库中验证。
- 模块没有父路由、API、数据库、环境变量、Docker 服务或运行数据。
- PR 前可逆序 `git revert` R2 模块专属提交。
- 合并后可单独 revert R2 PR 回到 R1；现有 `/matrix-oasis` 占位页保持不变。
- 未经用户明确要求，不删除 R0/R1/R2 分支或 worktree。

任何需要修改父仓文件的提案必须先填写 [`docs/PARENT_CHANGE_REQUEST_TEMPLATE.md`](./docs/PARENT_CHANGE_REQUEST_TEMPLATE.md) 并经用户人工批准。
