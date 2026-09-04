# AGENTS.md — 矩阵绿洲独立实验模块

本文件适用于 `experiments/matrix-oasis-engine/**`，并在父级 `AGENTS.md` 基础上增加更严格的隔离规则。

## 强制边界

1. 只修改本目录；任何父仓文件变更必须先取得用户明确批准。
2. 禁止依赖父 `client/`、`server/`、根配置、数据库、Docker、CI、路由、资产或构建产物。
3. 禁止模块外 `file:` / `link:`、符号链接、绝对路径或目录穿越。
4. 不提交密钥、真实 `.env`、日志、依赖目录、构建产物、Godot缓存、测试报告或生成资产。
5. 一批只解决一个可验证目标；先验证后提交；失败不得进入下一批。
6. 回退只使用 `git revert`，不得重置、覆盖或清理用户工作区。

## R22 专属限制

- R22只实现玩家显式触发的单轮AI对白和已有安全Action提案；不得创建任务、Action、Runtime节点、世界事件、模型工具循环、动画、语音或Creator接线。
- Runtime仍是游戏状态转换唯一权威，R19 Ledger仍是裁决历史唯一权威，R21只是可重建派生状态。模型不得直接写Runtime、Ledger、Persona、Memory或Relationship。
- AI候选必须是R19 grant、R20 rule、当前可用Action和当前可见绑定actor的交集；模型只看到opaque choice ID，外部Policy只能缩窄权限。
- 每个Turn最多一次Provider请求、零自动重试；必须先持久化预算预留和dispatch状态，并取得与完整外发内容绑定的当次人工批准。
- 普通verify、假Provider、拒绝审批和预算失败必须零网络、零凭据读取。真实OpenAI调用只允许使用精确Responses endpoint和模型锁，并须在调用前另行取得用户明确批准。
- 原始玩家文本、完整上下文、payload、模型对白、response ID、凭据和底层异常不得持久化到Receipt、Ledger、R21、日志或diagnostics。
- R1–R21、R16默认入口、R19合同与R20既有预览全部冻结；只允许R22精确白名单以及`r20-host-core.mjs`中默认行为不变的selector注入缝。
- 不引入第三方生产依赖、外部记忆/索引、数据库或embedding；参考项目只能按固定来源进行只读二次核查。
- `docs/MVP_STATUS.json`继续保留R16已资格结论；`docs/V2_STATUS.json`在R25前必须保持`claimAllowed=false`。
- 不push、不创建PR，直至用户明确批准R22验收和PR。
- 不删除或复用其他分支/worktree，不重建共享栈。主线前进时先报告差异，不擅自rebase。

## 提交前检查

```powershell
npm.cmd run verify
npm.cmd run check:round-scope
git status --short
git diff --cached --name-only
```

所有相对固定基线的变更路径必须以 `experiments/matrix-oasis-engine/` 开头。
