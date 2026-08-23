# AGENTS.md — 矩阵绿洲独立实验模块

本文件适用于 `experiments/matrix-oasis-engine/**`，并在父级 `AGENTS.md` 基础上增加更严格的隔离规则。

## 强制边界

1. 只修改本目录；任何父仓文件变更必须先取得用户明确批准。
2. 禁止依赖父 `client/`、`server/`、根配置、数据库、Docker、CI、路由、资产或构建产物。
3. 禁止模块外 `file:` / `link:`、符号链接、绝对路径或目录穿越。
4. 不提交密钥、真实 `.env`、日志、依赖目录、构建产物、Godot缓存、测试报告或生成资产。
5. 一批只解决一个可验证目标；先验证后提交；失败不得进入下一批。
6. 回退只使用 `git revert`，不得重置、覆盖或清理用户工作区。

## R16 专属限制

- R16只将冻结的R13分析、R14求解/物理复验与R15运行证据接入Creator；不得复制、调参或修改其算法、Godot场景、Runtime、资产与供应商适配器。
- R1–R15合同、验证器、编译器、Scene/Spatial格式、examples、vendor、ADR和历史验收全部冻结；仅R16精确白名单可修改。
- 旧source、solved或evidence缓存只能作为待资格输入；只有完整、身份闭合的R16 Qualification才可成为Creator `ready`或替换current。
- 每个唯一Solution首次必须完成R15完整证据；同一已资格Solution的缓存启动只能复验合同、身份和哈希，不得读取供应商凭据或联网。
- R15候选修复仍限最多两轮candidate-exclusion-only；不得改Intent、阈值、资产、Runtime、案例坐标或生成新资产。
- 普通verify、双真实缓存与合成拓扑资格均零网络、零供应商凭据、零费用；资格、媒体、日志和运行目录只允许存于`C:\tmp`。
- R16.7前`docs/MVP_STATUS.json`保持`pending-creator-migration / claimAllowed=false`；只有用户人工确认两类真实案例后才能切换默认入口和声明门。
- 不push、不创建PR，直至用户明确回复“R16验收通过，可以创建PR”。
- 不删除或复用其他分支/worktree，不重建共享栈。主线前进时先报告差异，不擅自rebase。

## 提交前检查

```powershell
npm.cmd run verify
npm.cmd run check:round-scope
git status --short
git diff --cached --name-only
```

所有相对固定基线的变更路径必须以 `experiments/matrix-oasis-engine/` 开头。
