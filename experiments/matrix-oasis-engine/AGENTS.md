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

## R0 专属限制

- R0 只建立隔离基线和 Creator 空壳。
- 不定义 Game Pack、Runtime Pack、AI Provider 或工具箱适配协议。
- 不创建或下载 Godot 项目、脚本、插件、模板或二进制。
- 不 push、不创建 PR，直至用户明确回复“R0 验收通过，可以创建 PR”。
- 未经用户明确要求，不删除 R0 分支或 worktree。
- 若主线在人工验收后前进，先报告差异，不擅自 rebase；任何 rebase 或冲突解决都必须全量重验并重新取得人工确认。
- 任何主仓或共享栈容器重建都必须先由用户确认时间窗口和共享基线；R0 默认不触碰共享栈。
- 遇到并行分支冲突时优先在本模块独立 preview 验收；验收通过后仍须重新核对基线与完整差异，方可进入 PR 审批。

## 提交前检查

```powershell
npm.cmd run verify
git status --short
git diff --cached --name-only
```

所有相对固定基线的变更路径必须以 `experiments/matrix-oasis-engine/` 开头。
