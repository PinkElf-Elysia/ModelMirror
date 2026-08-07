# R0 验收记录

状态：待实施与人工验收

固定基线：将在刷新 `origin/main` 后记录

最终 HEAD：仅在仓外交付清单与交付消息中记录，避免文档自引用

## 成功定义

- [ ] 全部变更位于 `experiments/matrix-oasis-engine/**`。
- [ ] Creator 可在模块根独立安装、构建、测试和 preview。
- [ ] Doctor 如实报告 Node/npm/Git 与 Godot 状态。
- [ ] 边界正向与全部负向用例通过。
- [ ] subtree 拆分后从空依赖状态完成相同验证。
- [ ] 父仓入口、源码、配置、Docker、CI 与 Matrix Oasis 占位页零差异。
- [ ] 父前端基线构建通过。
- [ ] 浏览器桌面与移动宽度人工冒烟通过。
- [ ] 用户完成人工验收前没有 push 或 PR。
- [ ] 未经用户明确要求，没有删除 R0 worktree 或分支。
- [ ] 没有重建或触碰共享栈容器；未来操作须先确认时间窗口和共享基线。

## 批次提交

| 批次 | 目标 | 提交 SHA | 结果 |
| --- | --- | --- | --- |
| R0.1 | 治理与隔离契约 | 待填 | 待验收 |
| R0.2 | Creator 独立空壳 | 待填 | 待验收 |
| R0.3 | Doctor 与边界护栏 | 待填 | 待验收 |
| R0.4 | 历史保留型拆分 | 待填 | 待验收 |
| R0.5 | 证据收口 | 本文不自引用 | 待验收 |

## 验证证据

| 命令 | 退出码 | 通过数量/关键结果 |
| --- | ---: | --- |
| `npm ci` | 待填 | 待填 |
| `npm prefix` | 待填 | 待填 |
| `npm ls --all` | 待填 | 待填 |
| `npm run doctor -- --json` | 待填 | 待填 |
| `npm run doctor:godot` | 预期非零 | Godot 尚未就绪 |
| `npm test` | 待填 | 待填 |
| `npm run check:boundary` | 待填 | 待填 |
| `npm run verify` | 待填 | 待填 |
| `npm run verify:extraction` | 待填 | 待填 |
| 父 `client` 构建 | 待填 | 待填 |

## 拆分证据

- Split tree SHA：待填
- Source archive SHA-256：待填
- 临时产物：只在仓外保存

## Godot 状态

- R0 是否需要 Godot：否
- 诊断结果：待填
- 后续门槛：Godot 4.6.x，可通过仓库外 `GODOT_BIN` 或 PATH 提供

## 未运行项与原因

- 待填。

## 风险与已知限制

见 [`../KNOWN_LIMITATIONS.md`](../KNOWN_LIMITATIONS.md)。

## 回退

1. PR 前按 R0.5 → R0.1 逆序 `git revert <sha>`。
2. 合并后 revert 整个 R0 PR 或五个模块专属提交。
3. 不需要恢复数据库、路由、环境变量、Docker、服务、Godot 资产或运行数据。
4. 回退后确认 `/matrix-oasis` 仍为固定基线中的原占位页。

## 人工验收门

只有用户明确回复“R0 验收通过，可以创建 PR”后才允许 push 和创建 draft PR。批准后若源文件、lockfile、脚本或文档变化，批准立即失效，必须重新完成全量验证与人工确认。

若主线在验收后前进，先报告差异，不擅自 rebase。任何 rebase 或冲突解决都会使原批准失效，必须全量重验并重新取得人工确认。

并行分支冲突优先在本模块独立 preview 验收；验收通过后仍须核对基线与完整 diff，才可进入 PR 审批。
