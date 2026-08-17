# AGENTS.md — 矩阵绿洲独立实验模块

本文件适用于 `experiments/matrix-oasis-engine/**`，并在父级 `AGENTS.md` 基础上增加更严格的隔离规则。

## 强制边界

1. 只修改本目录；任何父仓文件变更必须先取得用户明确批准。
2. 禁止依赖父 `client/`、`server/`、根配置、数据库、Docker、CI、路由、资产或构建产物。
3. 禁止模块外 `file:` / `link:`、符号链接、绝对路径或目录穿越。
4. 不提交密钥、真实 `.env`、日志、依赖目录、构建产物、Godot缓存、测试报告或生成资产。
5. 一批只解决一个可验证目标；先验证后提交；失败不得进入下一批。
6. 回退只使用 `git revert`，不得重置、覆盖或清理用户工作区。

## R13 专属限制

- R13只建立空间语义意图和Godot环境事实，不实现布局求解器，不切换Creator或现有预览，不改善R12成品布局，也不新增AI NPC、记忆、任务、世界事件、动画、语音、战斗、存档、多人、导出或父项目接入。
- R1–R12的合同、验证器、编译器、Runtime、Scene/Spatial格式、examples、Creator、现有Godot产品场景、vendor、ADR和验收记录全部字节冻结；仅机器白名单内的新合同、离线分析器、非执行参考摘录、测试和R13文档可修改。
- 一方源码不得包含末班地铁ID、文案、案例坐标、150 mm落地常量、供应商字段或网络能力。分析只应用输入Bundle声明的校准变换，并以Godot碰撞、导航和物理查询为事实权威。
- Spatial Intent只表达语义约束，不包含最终坐标。Environment Facts固定Godot右手Y-up、毫米、Euler YXZ和半径350 mm、高度1800 mm、floor snap 200 mm、最大坡度45°的玩家profile。
- Godot GLB场景解析与导航source geometry解析必须在主线程；导航烘焙使用异步接口；ray和capsule净空查询只在物理同步阶段运行。不得用headless结果冒充图形验收。
- 普通verify只使用合成夹具或经复验的仓外缓存，不联网、不产生费用、不读取供应商凭据。分析原始输出、调试捕获、真实facts、截图和日志只允许存于 `C:\tmp`。
- `docs/MVP_STATUS.json`与`check:mvp-claim`继续保持`pending-spatial-solver`和`claimAllowed=false`。R13只证明事实可提取；R14求解器及两类案例通过前不得宣称初版闭环完成。
- 不push、不创建PR，直至用户明确回复“R13验收通过，可以创建PR”。
- 不删除或复用其他分支/worktree，不重建共享栈。主线前进时先报告差异，不擅自rebase。

## 提交前检查

```powershell
npm.cmd run verify
npm.cmd run check:round-scope
git status --short
git diff --cached --name-only
```

所有相对固定基线的变更路径必须以 `experiments/matrix-oasis-engine/` 开头。
