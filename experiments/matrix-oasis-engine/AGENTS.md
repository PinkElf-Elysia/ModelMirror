# AGENTS.md — 矩阵绿洲独立实验模块

本文件适用于 `experiments/matrix-oasis-engine/**`，并在父仓 `AGENTS.md` 基础上增加更严格的隔离规则。

## 强制边界

1. 只修改本目录；任何父仓文件变更必须先提交父项目变更申请并取得用户人工批准。
2. 禁止依赖父 `client/`、`server/`、根配置、环境变量、数据库、Docker、CI、路由、资产或构建产物。
3. 禁止模块外 `file:` / `link:`、符号链接、绝对路径或目录穿越。
4. 不提交密钥、真实 `.env`、日志、依赖目录、构建产物、Godot 缓存、测试报告或二进制。
5. 一批只解决一个可验证目标；先验证后提交；失败不得进入下一批。
6. 回退只使用 `git revert`，不得重置、覆盖或清理用户工作区。

## R8 专属限制

- R8 只实现纯文本到严格 Generation Proposal、Authoring Pack、私有 Scene Blueprint 和 Runtime 验证；不生成资产、不启动 Godot、不修改 Creator。
- R1–R7 的 apps、examples、既有 packages、Godot、vendor、历史 ADR/验收和语义测试全部字节冻结；发现问题必须停报并单独申请修复。
- 只有 `packages/prototype-generator/src/openai-compatible.mjs` 可以使用受控 `fetch`；Creator、Godot、其他 package 与脚本仍禁止外部网络。
- Provider package 只接受调用方注入的配置，不读取环境；专用环境变量只能由生成 CLI 读取，且不得读取父仓 `LLM_GATEWAY_*` 或其他既有密钥变量。
- 输入仅允许最大 32 KiB fatal UTF-8 纯文本；禁止图片、全景、视频、3D 文件、目录或父仓数据。
- 最多一次初始请求与两次修复请求；禁止工具调用、流式输出、自动网络重试和无限 Agent 循环。
- R8 不得查询或调用 Marble/Meshy，不读取其凭据、额度或任务状态。
- 真实模型资格验证必须逐次获得用户人工批准；批准前只允许 loopback 假 Provider。
- 生成物、模型响应和详细日志只放 `C:\tmp` 的新目录；仓内不得提交真实输出或凭据。
- 每次验证使用固定 R8 基线 `21cbbb8b943b6f9d9799f014c44a6349e6124a63`；committed、staged、unstaged、untracked 一视同仁。
- 不 push、不创建 PR，直至用户明确回复“R8验收通过，可以创建PR”。
- 不删除 R0–R8 分支/worktree；不重建共享栈。主线前进时先报告差异，不擅自 rebase。

## 提交前检查

```powershell
npm.cmd run verify
npm.cmd run check:round-scope
git status --short
git diff --cached --name-only
```

所有相对固定基线的变更路径必须以 `experiments/matrix-oasis-engine/` 开头。
