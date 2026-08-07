# 矩阵绿洲 AI 原生 3D 游戏引擎（实验模块）

这是模镜仓库中的独立实验模块。R0 只证明模块可以独立开发、运行、验证、拆分和回退，不代表游戏引擎功能已经实现。

## R0 当前范围

- 建立可机器检查的模块边界与治理规则。
- 提供一个自包含的 Creator Web 空壳。
- 提供本地环境诊断、隔离检查和历史保留型拆分验证。
- 将 Godot 4.6.x 记录为后续轮次的可选前置工具。

R0 不包含 Game Pack、Validator、Compiler、Runtime Pack、AI、NPC、RAG、MCP、Godot 工程、3D 内容、Tauri、父项目接入、部署或发布。

## 独立性约束

- 父项目交互为 `none`，白名单为空。
- 不依赖父仓源码、配置、环境变量、数据库、Docker、路由、资产或 `node_modules`。
- 模块拥有自己的 npm 根、lockfile 和验证脚本。
- Creator 源码无网络访问；验证脚本仅可访问 loopback。
- 内部实验使用 `UNLICENSED`，不发布 npm 包。

机器可读规则位于 [`module-boundary.json`](./module-boundary.json)，详细说明见 [`docs/BOUNDARIES.md`](./docs/BOUNDARIES.md)。

## 未来方向（非 R0 接口）

```text
Creator
→ Authoring Game Pack
→ Validator / Compiler
→ Immutable Runtime Pack
→ Godot Runtime
```

这条链路只表达组件方向。R0 不定义任何 Pack 字段、Schema 或通信协议。

## 独立运行与验证

在模块根执行：

```powershell
npm.cmd ci
npm.cmd run dev
npm.cmd run verify:creator
npm.cmd run doctor
npm.cmd run --silent doctor -- --json
npm.cmd run check:boundary
npm.cmd test
npm.cmd run verify
```

其中带 `--silent` 的 doctor 命令是机器可解析入口：其标准输出只包含一个 JSON 文档，不带 npm 生命周期前缀。普通 `npm.cmd run doctor` 保留为供人工阅读的诊断输出。

缺少 Godot 不会阻塞 R0，普通 doctor 返回 `ready_with_warnings`。用于后续轮次的严格检查会如实失败：

```powershell
npm.cmd run doctor:godot
```

历史保留型拆分演练：

```powershell
npm.cmd run verify:extraction
```

仅在父仓 worktree 中执行固定基线范围保护：

```powershell
npm.cmd run check:parent-scope -- --base 952f8094c38b29baffa5de3a5b0caa94e501f45f
```

该命令会把参数与 `module-boundary.json` 中机器固定的 R0 基线比较，不能通过传入较新的提交缩短差异范围。拆分后的 standalone 仓明确不运行这一父仓专用命令。

该命令只从干净 worktree 克隆当前 HEAD，在一次性临时目录执行 `git subtree split`，并在拆分仓库根从空依赖完成安装、完整验证与 source-only archive 哈希。成功后只清理自身创建的临时目录；失败时保留并报告精确诊断目录。

## 拆分与回退

- 历史保留型拆分使用 `git subtree split --prefix=experiments/matrix-oasis-engine`，并在一次性临时仓库中验证。
- 模块没有父路由、API、数据库、环境变量、Docker 服务或运行数据。
- PR 前可逆序 `git revert` 本轮五个模块专属提交。
- 合并后可 revert R0 PR；删除本目录即可移除全部 R0 功能，现有 `/matrix-oasis` 占位页保持不变。
- 未经用户明确要求，不删除 R0 分支或 worktree。

任何需要修改父仓文件的提案必须先填写 [`docs/PARENT_CHANGE_REQUEST_TEMPLATE.md`](./docs/PARENT_CHANGE_REQUEST_TEMPLATE.md) 并经用户人工批准。
