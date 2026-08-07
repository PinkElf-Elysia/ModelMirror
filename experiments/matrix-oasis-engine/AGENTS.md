# AGENTS.md — 矩阵绿洲独立实验模块

本文件适用于 `experiments/matrix-oasis-engine/**`，并在父仓 `AGENTS.md` 基础上增加更严格的隔离规则。

## 强制边界

1. 只修改本目录。任何父仓文件变更都必须先填写 `docs/PARENT_CHANGE_REQUEST_TEMPLATE.md` 并取得用户人工批准。
2. 禁止导入或复制依赖父仓 `client/`、`server/`、根配置、根环境变量、数据库、Docker、CI、路由或构建产物。
3. 禁止通过 `file:`、`link:`、符号链接、绝对路径或目录穿越引用模块外文件。
4. 本模块拥有独立 manifest、lockfile、工具脚本与验证命令，不注册到父仓 workspace。
5. Creator 源码不得访问网络、读取环境变量或写入浏览器/文件持久化；验证脚本只可访问 loopback。
6. 不提交密钥、真实 `.env`、日志、依赖目录、构建产物、覆盖率、缓存、Godot 生成目录或二进制。
7. 先写验收，再实现；一批只解决一个可验证目标；失败批次不得进入下一批。
8. 回退使用 `git revert`，不得重置、覆盖或清理用户工作区。

## R2 专属限制

- R2 主线只允许建设案例无关的确定性参考模拟器与 Creator 最小运行实验台；R2.1 仅升级治理，不实现模拟器 API，后续批次严格遵循已批准的公开接口与语义。
- `packages/game-pack-contracts/**`、`packages/game-pack-validator/**`、`examples/**`、R1 权威合同说明、R0/R1 历史 ADR 与验收记录字节冻结；若实现需要修改这些路径，必须停止并重新申请范围审批。
- R2 允许变更范围采用机器可读正向 allowlist。除指定根文件、`apps/creator-web/**`、`packages/game-pack-simulator/**`、`scripts/**`、`tests/**` 和非冻结 `docs/**` 外，其他模块路径一律阻断。
- 样例只作为通用语义、可视化和验收输入，不得驱动题材专属逻辑或成品叙事打磨。
- 参考模拟器不是 Compiler、Runtime Pack 或生产运行时；不得加入网络、持久化、随机隐式状态或外部 Provider。
- 不创建或下载 Godot 项目、脚本、插件、模板或二进制。
- 每次验证必须执行固定 R2 基线范围检查；冻结路径、未列入 allowlist 的模块路径或模块外变更，无论 committed、staged、unstaged、untracked 均阻断。
- 不 push、不创建 PR，直至用户明确回复“R2验收通过，可以创建PR”。
- 未经用户明确要求，不删除 R0/R1/R2 分支或 worktree。
- 若主线在人工验收后前进，先报告差异，不擅自 rebase；任何 rebase 或冲突解决都必须全量重验并重新取得人工确认。
- 任何主仓或共享栈容器重建都必须先由用户确认时间窗口和共享基线；R2 默认不触碰共享栈。
- 遇到并行分支冲突时优先在本模块独立 preview 验收；验收通过后仍须重新核对基线与完整差异，方可进入 PR 审批。

## 提交前检查

```powershell
npm.cmd run verify
npm.cmd run check:round-scope
git status --short
git diff --cached --name-only
```

所有相对固定基线的变更路径必须以 `experiments/matrix-oasis-engine/` 开头。
