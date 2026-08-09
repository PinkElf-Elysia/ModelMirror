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

## R3 专属限制

- R3 主线只建设确定性 Compiler、不可变 Runtime Pack、独立 Runtime Simulator 与黑盒语义等价验证；R3.1 仅升级治理，不实现这些 API。
- R1 contracts、Validator、CLI、examples，R2 Simulator 及其语义测试，以及 R0-R2 历史 ADR/验收记录字节冻结；发现问题必须停报并单独申请修复。
- R2 Simulator 只能从 workspace 包根作为黑盒 oracle 调用；禁止导入其 `src/**`、抽取 evaluator 或共享 condition/effect 执行核。
- schema v3 采用机器可读正向 allowlist：既有 app、docs、scripts、tests 只允许精确文件，新建代码只允许落入五个批准的 R3 package 前缀；未知路径失败关闭。
- Creator 只在 R3.5 按精确文件白名单解冻；样例只作为通用语义、差分和可视化验收输入，不得驱动题材专属逻辑或叙事打磨。
- 不创建或下载 Godot 工程、脚本、插件、模板、二进制；不接父项目、网络、环境变量、持久化或外部 Provider。
- 每次验证必须执行固定 R3 基线 `380c747e62193855c724a947d99a84070ca623ff` 范围检查；committed、staged、unstaged、untracked 一视同仁。
- 不 push、不创建 PR，直至用户明确回复“R3 验收通过，可以创建PR”。
- 未经用户明确要求，不删除 R0-R3 分支或 worktree；主线前进时先报告差异，不擅自 rebase。
- 任何主仓或共享栈容器重建都必须先由用户确认时间窗口和共享基线；R3 默认只用独立 preview。

## 提交前检查

```powershell
npm.cmd run verify
npm.cmd run check:round-scope
git status --short
git diff --cached --name-only
```

所有相对固定基线的变更路径必须以 `experiments/matrix-oasis-engine/` 开头。
