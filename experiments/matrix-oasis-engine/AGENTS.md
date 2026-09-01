# AGENTS.md — 矩阵绿洲独立实验模块

本文件适用于 `experiments/matrix-oasis-engine/**`，并在父级 `AGENTS.md` 基础上增加更严格的隔离规则。

## 强制边界

1. 只修改本目录；任何父仓文件变更必须先取得用户明确批准。
2. 禁止依赖父 `client/`、`server/`、根配置、数据库、Docker、CI、路由、资产或构建产物。
3. 禁止模块外 `file:` / `link:`、符号链接、绝对路径或目录穿越。
4. 不提交密钥、真实 `.env`、日志、依赖目录、构建产物、Godot缓存、测试报告或生成资产。
5. 一批只解决一个可验证目标；先验证后提交；失败不得进入下一批。
6. 回退只使用 `git revert`，不得重置、覆盖或清理用户工作区。

## R20 专属限制

- R20只实现固定策略NPC调度、R19兼容增量会话、隔离loopback协调器和隔离Godot实体桥；不得实现AI认知、对话、记忆、关系、动态任务、世界事件或角色动画。
- 现有Runtime仍是游戏状态转换的唯一权威；R20只允许对R19裁决内核做兼容抽取，不得改变R19合同、旧导出、diagnostic或canonical字节。
- R1–R19合同、验证器、编译器、Creator默认路径、R16预览、既有Godot产品场景、Scene/Spatial格式、examples、vendor、供应商适配器、ADR和历史验收全部冻结；仅R20精确白名单可修改。
- 普通verify和CLI必须离线；真实OpenAI、Marble、Meshy、父凭据、父API、共享栈、父Docker及其他worktree均禁止。
- 三个R20核心workspace不得读取文件、环境变量、网络或启动进程；只有R20宿主/CLI可以读取已命名输入、启动锁定Godot和向`C:\tmp`下事务发布。
- 外部项目仅作固定来源参考，不复制源码、不新增运行依赖；Beehave与LimboAI均不得进入产品依赖。
- `docs/MVP_STATUS.json`继续保留R16已资格结论；`docs/V2_STATUS.json`在R25前必须保持`claimAllowed=false`。
- R20隔离预览只允许玩家WASD观察；玩家终端不得写入NPC权威时间线，R16默认预览必须保持不变。
- 不push、不创建PR，直至用户明确回复“R20验收通过，可以创建PR”。
- 不删除或复用其他分支/worktree，不重建共享栈。主线前进时先报告差异，不擅自rebase。

## 提交前检查

```powershell
npm.cmd run verify
npm.cmd run check:round-scope
git status --short
git diff --cached --name-only
```

所有相对固定基线的变更路径必须以 `experiments/matrix-oasis-engine/` 开头。
