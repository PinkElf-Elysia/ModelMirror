# AGENTS.md — 矩阵绿洲独立实验模块

本文件适用于 `experiments/matrix-oasis-engine/**`，并在父仓 `AGENTS.md` 基础上增加更严格的隔离规则。

## 强制边界

1. 只修改本目录；任何父仓文件变更必须先提交父项目变更申请并取得用户人工批准。
2. 禁止依赖父 `client/`、`server/`、根配置、环境变量、数据库、Docker、CI、路由、资产或构建产物。
3. 禁止模块外 `file:` / `link:`、符号链接、绝对路径或目录穿越。
4. 不提交密钥、真实 `.env`、日志、依赖目录、构建产物、Godot 缓存、测试报告或二进制。
5. 一批只解决一个可验证目标；先验证后提交；失败不得进入下一批。
6. 回退只使用 `git revert`，不得重置、覆盖或清理用户工作区。

## R5 专属限制

- R5 只建立冻结 Runtime Pack/Receipt 到 Godot 的只读适配器、独立 GDScript 执行器、差分 harness 和最小调试 HUD；不做角色控制、Marble、AI、资产、存档或父项目接入。
- R1–R4 的 Creator、examples、全部 package、Bootstrap、GdUnit4 vendor、历史 ADR/验收记录及既有语义测试字节冻结；发现问题必须停报并单独申请修复。
- 新 Godot 第一方文件只允许落在 `apps/runtime-godot/runtime/**`、`apps/runtime-godot/test/r5/**` 与精确 `scenes/runtime_lab.tscn`；不得修改 R4 Bootstrap 或 addon。
- GdUnit4 必须保持上游 v6.2.0 指定 commit 原样；任何补丁、版本切换或许可证变化需要人工审批。
- Godot 可执行文件、导出模板和 MCP 工具只放仓外；仓内禁止 `.exe`、`.dll`、`.pck`、导出物和 MCP 配置。
- 自动验证只允许 headless 与自身 loopback；图形固定帧输出必须位于仓外临时目录。
- MCP 只在一次性仓外副本资格验证，不得操作正式 worktree，也不得使用凭据或非 loopback 网络。
- Runtime Pack 与 Receipt 只允许成对、本地、只读加载；第一方 Godot 仍禁止网络、进程、环境变量、动态脚本和文件写入。
- 每次验证使用固定 R5 基线 `d47f1b15e5610f41d4d9f3e5fe91966530a1a4be`；committed、staged、unstaged、untracked 一视同仁。
- 不 push、不创建 PR，直至用户明确回复“R5验收通过，可以创建PR”。
- 不删除 R0–R5 分支/worktree；不重建共享栈。主线前进时先报告差异，不擅自 rebase。

## 提交前检查

```powershell
npm.cmd run verify
npm.cmd run check:round-scope
git status --short
git diff --cached --name-only
```

所有相对固定基线的变更路径必须以 `experiments/matrix-oasis-engine/` 开头。
