# AGENTS.md — 矩阵绿洲独立实验模块

本文件适用于 `experiments/matrix-oasis-engine/**`，并在父级 `AGENTS.md` 基础上增加更严格的隔离规则。

## 强制边界

1. 只修改本目录；任何父仓文件变更必须先取得用户明确批准。
2. 禁止依赖父 `client/`、`server/`、根配置、数据库、Docker、CI、路由、资产或构建产物。
3. 禁止模块外 `file:` / `link:`、符号链接、绝对路径或目录穿越。
4. 不提交密钥、真实 `.env`、日志、依赖目录、构建产物、Godot缓存、测试报告或生成资产。
5. 一批只解决一个可验证目标；先验证后提交；失败不得进入下一批。
6. 回退只使用 `git revert`，不得重置、覆盖或清理用户工作区。

## R21 专属限制

- R21只实现由R19 World Event Ledger派生的静态persona绑定、结构化memory projection和显式relationship projection；不得实现模型认知、对话、动态任务、世界事件生成、动画或Creator/Godot产品接线。
- 现有Runtime仍是游戏状态转换的唯一权威，R19 Ledger仍是裁决历史、因果关系和来源证明的唯一权威；R21派生产物不得反向修改Runtime、Ledger、R19合同或R20调度。
- Persona是可信、版本化、闭合字段的静态seed，不从Ledger推断且R21内不得演化。Memory只包含actor自身已接受Action的结构化episode。Relationship只来自显式policy的精确Action映射，使用定向有界整数delta；拒绝事件默认不贡献。
- R21只支持单timeline。跨reset、跨timeline聚合、选择性forget和correction均不支持，也不得宣称；删除仅指删除全部派生artifact/index后从同一Ledger字节级重建。
- R1–R20合同、实现、Creator默认路径、Godot场景、Runtime/Scene/Spatial格式、examples、vendor、供应商适配器、ADR和历史验收全部冻结；仅R21精确白名单可修改。
- 普通verify和CLI必须离线；不得读取真实OpenAI、Marble、Meshy或父凭据，不得调用父API、共享栈、父Docker或其他worktree。
- 不引入外部索引、数据库、embedding、自由文本解析或第三方生产依赖；参考项目只能按固定来源进行只读二次核查。
- `docs/MVP_STATUS.json`继续保留R16已资格结论；`docs/V2_STATUS.json`在R25前必须保持`claimAllowed=false`。
- 不push、不创建PR，直至用户明确批准R21验收和PR。
- 不删除或复用其他分支/worktree，不重建共享栈。主线前进时先报告差异，不擅自rebase。

## 提交前检查

```powershell
npm.cmd run verify
npm.cmd run check:round-scope
git status --short
git diff --cached --name-only
```

所有相对固定基线的变更路径必须以 `experiments/matrix-oasis-engine/` 开头。
