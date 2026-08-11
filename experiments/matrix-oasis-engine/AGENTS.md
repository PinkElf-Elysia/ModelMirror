# AGENTS.md — 矩阵绿洲独立实验模块

本文件适用于 `experiments/matrix-oasis-engine/**`，并在父仓 `AGENTS.md` 基础上增加更严格的隔离规则。

## 强制边界

1. 只修改本目录；任何父仓文件变更必须先提交父项目变更申请并取得用户人工批准。
2. 禁止依赖父 `client/`、`server/`、根配置、环境变量、数据库、Docker、CI、路由、资产或构建产物。
3. 禁止模块外 `file:` / `link:`、符号链接、绝对路径或目录穿越。
4. 不提交密钥、真实 `.env`、日志、依赖目录、构建产物、Godot 缓存、测试报告或二进制。
5. 一批只解决一个可验证目标；先验证后提交；失败不得进入下一批。
6. 回退只使用 `git revert`，不得重置、覆盖或清理用户工作区。

## R6 专属限制

- R6 只建立冻结 R5 Runtime 上的第一人称移动、射线交互、动态 Action 终端和最小可玩 3D 实验台；不做 NPC、导航、Marble、资产、AI、存档或父项目接入。
- R1–R5 的 Creator、examples、全部 package、Bootstrap、Runtime、R5 HUD、GdUnit4 vendor、历史 ADR/验收记录及既有语义测试字节冻结；发现问题必须停报并单独申请修复。
- 新 Godot 第一方文件只允许落在 `apps/runtime-godot/playable/**` 与 `apps/runtime-godot/test/r6/**`；`project.godot` 只允许增加已批准的 InputMap、Jolt 和物理插值设置，其他 R4/R5 文件不得修改。
- GdUnit4 必须保持上游 v6.2.0 指定 commit 原样；任何补丁、版本切换或许可证变化需要人工审批。
- Godot 可执行文件、导出模板和 MCP 工具只放仓外；仓内禁止 `.exe`、`.dll`、`.pck`、导出物和 MCP 配置。
- 自动验证只允许 headless 与自身 loopback；图形固定帧输出必须位于仓外临时目录。
- MCP 只在一次性仓外副本资格验证，不得操作正式 worktree，也不得使用凭据或非 loopback 网络。
- Runtime Pack 与 Receipt 只允许成对、本地、只读加载；第一方 Godot 仍禁止网络、进程、环境变量、动态脚本和文件写入。
- 官方 demo 只允许以非 Godot 可执行参考文件、MIT License、来源锁和适配说明进入 `third-party/godot-demo-projects/**`，不得直接执行或静默修改。
- 每次验证使用固定 R6 基线 `430f24a4fd8510a0d54f14bcd240a80423d16719`；committed、staged、unstaged、untracked 一视同仁。
- 不 push、不创建 PR，直至用户明确回复“R6验收通过，可以创建PR”。
- 不删除 R0–R6 分支/worktree；不重建共享栈。主线前进时先报告差异，不擅自 rebase。

## 提交前检查

```powershell
npm.cmd run verify
npm.cmd run check:round-scope
git status --short
git diff --cached --name-only
```

所有相对固定基线的变更路径必须以 `experiments/matrix-oasis-engine/` 开头。
