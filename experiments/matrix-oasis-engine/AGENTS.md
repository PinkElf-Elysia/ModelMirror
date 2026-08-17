# AGENTS.md — 矩阵绿洲独立实验模块

本文件适用于 `experiments/matrix-oasis-engine/**`，并在父级 `AGENTS.md` 基础上增加更严格的隔离规则。

## 强制边界

1. 只修改本目录；任何父仓文件变更必须先取得用户明确批准。
2. 禁止依赖父 `client/`、`server/`、根配置、数据库、Docker、CI、路由、资产或构建产物。
3. 禁止模块外 `file:` / `link:`、符号链接、绝对路径或目录穿越。
4. 不提交密钥、真实 `.env`、日志、依赖目录、构建产物、Godot缓存、测试报告或生成资产。
5. 一批只解决一个可验证目标；先验证后提交；失败不得进入下一批。
6. 回退只使用 `git revert`，不得重置、覆盖或清理用户工作区。

## R14 专属限制

- R14只实现Spatial Intent离线合成、确定性约束求解、隔离Godot物理复验和已求解预览；不新增AI NPC、记忆、任务、世界事件、动画、语音、战斗、存档、多人、导出或父项目接入。
- R1–R13的合同、验证器、编译器、Runtime、Scene/Spatial格式、examples、历史Creator/Godot产品场景、vendor、ADR和验收记录全部字节冻结；仅机器白名单内的新合同、求解器、复验器、隔离预览、测试和R14文档可修改。
- 一方源码不得包含末班地铁ID、文案、案例坐标、供应商字段或网络能力。求解只消费严格复验的Intent、Facts、Runtime、Receipt和Asset Bundle，不允许随机、时间驱动退出、部分成功或旧AABB网格回退。
- Spatial Solution固定Godot右手Y-up、毫米、Euler YXZ；玩家、terminal、clearance、候选和搜索上限只来自机器策略与合同，不得隐藏漂移。
- Godot最终复验必须等待NavigationServer与physics同步，使用真实collider、资产和Action terminal执行path、capsule、接地、重叠、穿透和视线检查。不得用headless结果冒充图形验收。
- 普通verify只使用合成夹具或经复验的仓外缓存，不联网、不产生费用、不读取供应商凭据。facts、solution、overlay、调试捕获、截图和日志只允许存于 `C:\tmp`。
- `docs/MVP_STATUS.json`与`check:mvp-claim`继续保持`pending-spatial-solver`和`claimAllowed=false`。只有用户明确确认R11中性案例与R12末班地铁全部人工门通过后，R14.7才可切换Creator默认预览并解除声明门。
- 不push、不创建PR，直至用户明确回复“R14验收通过，可以创建PR”。
- 不删除或复用其他分支/worktree，不重建共享栈。主线前进时先报告差异，不擅自rebase。

## 提交前检查

```powershell
npm.cmd run verify
npm.cmd run check:round-scope
git status --short
git diff --cached --name-only
```

所有相对固定基线的变更路径必须以 `experiments/matrix-oasis-engine/` 开头。
