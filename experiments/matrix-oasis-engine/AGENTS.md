# AGENTS.md — 矩阵绿洲独立实验模块

本文件适用于 `experiments/matrix-oasis-engine/**`，并在父仓 `AGENTS.md` 基础上增加更严格的隔离规则。

## 强制边界

1. 只修改本目录；任何父仓文件变更必须先提交父项目变更申请并取得用户人工批准。
2. 禁止依赖父 `client/`、`server/`、根配置、环境变量、数据库、Docker、CI、路由、资产或构建产物。
3. 禁止模块外 `file:` / `link:`、符号链接、绝对路径或目录穿越。
4. 不提交密钥、真实 `.env`、日志、依赖目录、构建产物、Godot 缓存、测试报告或二进制。
5. 一批只解决一个可验证目标；先验证后提交；失败不得进入下一批。
6. 回退只使用 `git revert`，不得重置、覆盖或清理用户工作区。

## R9 专属限制

- R9 只把冻结的 R8 Scene Blueprint 物化为真实道具/静态人物 GLB、离线规范化结果和私有 Asset Bundle；环境继续使用冻结的 Kenney 模板，不实现 R10 自动布局。
- R1–R8 的 apps、examples、既有 packages、Godot、vendor、历史 ADR/验收和语义测试全部字节冻结；发现问题必须停报并单独申请修复。
- 网络例外仅有冻结的 R8 OpenAI 适配器和 `packages/prototype-asset-pipeline/src/meshy-provider.mjs`。Creator、Godot、其他 package 与脚本仍禁止外部网络。
- Meshy provider 只接受调用方注入配置，不读取环境。只有资格 CLI 可读取 `MATRIX_OASIS_MESHY_API_KEY`，且不得打印、持久化或复制它。
- Marble 在 R9 继续完全禁用；不得查询额度、创建任务、轮询或下载。不得把 collider GLB 当作环境视觉网格，也不得引入 SPZ/Splat。
- 真实 Meshy 的 create、poll、download 必须按任务和阶段分别披露并取得一次性人工批准；普通测试仅允许 loopback 假服务且不得产生费用。
- 原始响应、任务状态、下载 URL、供应商资产和详细日志只放 `C:\tmp`；仓内只提交适配器、离线测试、脱敏说明和不含供应商标识的合同。
- Sharp/libvips 仅作为模块本地离线开发工具，适用用户批准的 LGPL-3.0-or-later 例外；不得 vendoring 二进制，也不得进入 Creator、Godot 或 runtime 分发。
- 每次验证使用固定 R9 基线 `da5fd0fe39234807ae3c4a1d543b9fd64de66d97`；committed、staged、unstaged、untracked 一视同仁。
- 不 push、不创建 PR，直至用户明确回复“R9验收通过，可以创建PR”。
- 不删除或复用其他分支/worktree，不重建共享栈。主线前进时先报告差异，不擅自 rebase。

## 提交前检查

```powershell
npm.cmd run verify
npm.cmd run check:round-scope
git status --short
git diff --cached --name-only
```

所有相对固定基线的变更路径必须以 `experiments/matrix-oasis-engine/` 开头。
