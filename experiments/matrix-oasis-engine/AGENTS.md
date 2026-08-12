# AGENTS.md — 矩阵绿洲独立实验模块

本文件适用于 `experiments/matrix-oasis-engine/**`，并在父仓 `AGENTS.md` 基础上增加更严格的隔离规则。

## 强制边界

1. 只修改本目录；任何父仓文件变更必须先提交父项目变更申请并取得用户人工批准。
2. 禁止依赖父 `client/`、`server/`、根配置、环境变量、数据库、Docker、CI、路由、资产或构建产物。
3. 禁止模块外 `file:` / `link:`、符号链接、绝对路径或目录穿越。
4. 不提交密钥、真实 `.env`、日志、依赖目录、构建产物、Godot 缓存、测试报告或二进制。
5. 一批只解决一个可验证目标；先验证后提交；失败不得进入下一批。
6. 回退只使用 `git revert`，不得重置、覆盖或清理用户工作区。

## R10 专属限制

- R10 只完成纯文本到 panorama 环境、collider、冻结 Meshy 资产、确定性 Scene Pack 与一键 Godot 预览；不接 SPZ、HQ mesh、AI NPC、存档或父产品。
- R1–R9 的合同、Runtime、Scene Pack、examples、vendor、历史 ADR/验收和语义测试全部字节冻结；仅本轮明确列出的 Creator 文件与新 Godot wrapper 解冻。
- 网络例外仅有冻结的 R8 OpenAI adapter、冻结的 R9 Meshy adapter 和 `packages/prototype-environment-pipeline/src/marble-provider.mjs`。Creator、Godot和其他代码仍禁止外部网络。
- Provider 只接受调用方注入配置。只有本地宿主/资格 CLI 可读取 `MATRIX_OASIS_MODEL_*`、`MATRIX_OASIS_MESHY_API_KEY`、`MATRIX_OASIS_MARBLE_API_KEY` 与 `GODOT_BIN`，且不得返回或持久化其值。
- Marble 固定 `marble-1.1` 纯文本输入，只消费 panorama PNG 与 collider GLB；SPZ、网页 HQ mesh 和父服务凭据存储均禁止。
- 真实模型和 Marble/Meshy 操作必须由当前内容哈希绑定的人工审批覆盖；普通 verify 只能使用 loopback 或已验证仓外缓存，不产生费用。
- 原始响应、任务状态、下载 URL、供应商资产和详细日志只放 `C:\tmp`；仓内只提交适配器、离线测试、脱敏说明和不含供应商标识的合同。
- Sharp/libvips 仅作为模块本地离线开发工具，适用用户批准的 LGPL-3.0-or-later 例外；不得 vendoring 二进制，也不得进入 Creator、Godot 或 runtime 分发。
- 每次验证使用固定 R10 基线 `09f4cca4f1e02fe275ada17535597437cac3778d`；committed、staged、unstaged、untracked 一视同仁。
- 不 push、不创建 PR，直至用户明确回复“R10验收通过，可以创建PR”。
- 不删除或复用其他分支/worktree，不重建共享栈。主线前进时先报告差异，不擅自 rebase。

## 提交前检查

```powershell
npm.cmd run verify
npm.cmd run check:round-scope
git status --short
git diff --cached --name-only
```

所有相对固定基线的变更路径必须以 `experiments/matrix-oasis-engine/` 开头。
