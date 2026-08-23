# AGENTS.md — 矩阵绿洲独立实验模块

本文件适用于 `experiments/matrix-oasis-engine/**`，并在父级 `AGENTS.md` 基础上增加更严格的隔离规则。

## 强制边界

1. 只修改本目录；任何父仓文件变更必须先取得用户明确批准。
2. 禁止依赖父 `client/`、`server/`、根配置、数据库、Docker、CI、路由、资产或构建产物。
3. 禁止模块外 `file:` / `link:`、符号链接、绝对路径或目录穿越。
4. 不提交密钥、真实 `.env`、日志、依赖目录、构建产物、Godot缓存、测试报告或生成资产。
5. 一批只解决一个可验证目标；先验证后提交；失败不得进入下一批。
6. 回退只使用 `git revert`，不得重置、覆盖或清理用户工作区。

## R15 专属限制

- R15只在冻结R14 Solution/Verification之上增加实际InputMap重放、运行证据、媒体采集和最多两轮候选排除；不复制求解器或物理复验器。
- R1–R14的合同、验证器、编译器、Runtime、Scene/Spatial格式、Creator、既有Godot产品场景、vendor、ADR和历史验收全部冻结；仅R15精确白名单可修改。
- 重放必须通过`Input.parse_input_event()`进入实际控制器、RayCast3D与Action terminal；禁止直接调用Runtime action、trace捷径、合成移动接口或直接传送玩家。
- 自动修复仅允许排除R14 placement/station/terminal candidate并完整重求解、复验；不得改Spatial Intent语义、资产、Runtime、案例坐标或阈值。
- 普通verify与R11/R12缓存资格均不联网、不读取供应商凭据、不产生费用。Evidence、截图、录像和日志只允许存于`C:\tmp`。
- `docs/MVP_STATUS.json`与`check:mvp-claim`保持`pending-runtime-evidence`和`claimAllowed=false`。R15通过后只可迁移为等待R16，R15不得解除初版声明门。
- 不push、不创建PR，直至用户明确回复“R15验收通过，可以创建PR”。
- 不删除或复用其他分支/worktree，不重建共享栈。主线前进时先报告差异，不擅自rebase。

## 提交前检查

```powershell
npm.cmd run verify
npm.cmd run check:round-scope
git status --short
git diff --cached --name-only
```

所有相对固定基线的变更路径必须以 `experiments/matrix-oasis-engine/` 开头。
