# AGENTS.md — 矩阵绿洲独立实验模块

本文件适用于 `experiments/matrix-oasis-engine/**`，并在父级 `AGENTS.md` 基础上增加更严格的隔离规则。

## 强制边界

1. 只修改本目录；任何父仓文件变更必须先取得用户明确批准。
2. 禁止依赖父 `client/`、`server/`、根配置、数据库、Docker、CI、路由、资产或构建产物。
3. 禁止模块外 `file:` / `link:`、符号链接、绝对路径或目录穿越。
4. 不提交密钥、真实 `.env`、日志、依赖目录、构建产物、Godot缓存、测试报告或生成资产。
5. 一批只解决一个可验证目标；先验证后提交；失败不得进入下一批。
6. 回退只使用 `git revert`，不得重置、覆盖或清理用户工作区。

## R18 专属限制

- R18只补全第二版候选全景、分层资格、证伪证据和R19–R25路线；不得开发AI NPC、记忆、对话、动态事件或新的Creator功能。
- R1–R17合同、验证器、编译器、Creator产品路径、Godot产品场景、Scene/Spatial格式、examples、vendor、供应商适配器、ADR和历史验收全部冻结；仅R18精确白名单可修改。
- 候选完整源码、依赖、二进制、容器、模型和动画资产只允许位于`C:\tmp`；Git只保存来源锁、许可证、原创适配笔记、脱敏摘要和证据哈希。
- 普通verify必须离线；真实OpenAI、Marble和Meshy调用、父凭据读取、共享栈、父Docker及其他worktree均禁止。
- 公网发现只允许在披露主机和请求上限并取得当次批准后执行；不得登录、读取凭据或调用商业产品API。
- 候选默认禁止Docker。若关键结论确实依赖容器，必须逐候选披露镜像digest、端口、卷、网络、资源上限和清理方式并取得当次批准。
- `docs/MVP_STATUS.json`继续保留R16已资格结论；`docs/V2_STATUS.json`在R25前必须保持`claimAllowed=false`。
- R18只输出架构参考、可执行短名单、正式集成推荐、备选、延后或拒绝及切换条件，不把任何候选正式接入产品运行时。
- 不push、不创建PR，直至用户明确回复“R18验收通过，可以创建PR”。
- 不删除或复用其他分支/worktree，不重建共享栈。主线前进时先报告差异，不擅自rebase。

## 提交前检查

```powershell
npm.cmd run verify
npm.cmd run check:round-scope
git status --short
git diff --cached --name-only
```

所有相对固定基线的变更路径必须以 `experiments/matrix-oasis-engine/` 开头。
