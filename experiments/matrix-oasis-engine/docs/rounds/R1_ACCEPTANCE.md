# R1 验收记录

状态：R1.1 治理基线实施中；等待后续批次与最终人工验收

固定基线：`8deeebb85d2db1b7f1b3564fca984503ce5787a2`

最终 HEAD、split tree 与 archive SHA-256 只记录在仓外交付清单，避免本文自引用。

## 成功定义

- [ ] 全部 R1 变更位于 `experiments/matrix-oasis-engine/**`，且 `apps/creator-web/**` 零差异。
- [ ] 通用 Authoring Game Pack 可由单个 JSON 表达并由确定性验证器校验。
- [ ] 非法引用、重复 ID 与非法交互图返回稳定、可定位诊断。
- [ ] `npm run verify`、固定基线范围检查与历史保留型拆分全部通过。
- [ ] 父源码、配置、Matrix Oasis 页面和共享栈零改动。
- [ ] 用户完成最终人工验收。

## 批次

| 批次 | 目标 | 提交 | 状态 |
| --- | --- | --- | --- |
| R1.1 | 治理与隔离契约升级 | `a3a5c13` | 已完成 |
| R1.2 | 通用 Game Pack 合同 | 待记录 | 实施中 |
| R1.3 | 确定性验证器 | 待记录 | 未开始 |
| R1.4 | 单 JSON 验收样例与负向测试 | 待记录 | 未开始 |
| R1.5 | 拆分证据与人工验收包 | 待记录 | 未开始 |

## 验证证据

| 检查 | 状态 | 命令 | 结果 |
| --- | --- | --- | --- |
| 依赖树 | 未运行 | `npm.cmd ci && npm.cmd ls --all` | 待记录 |
| 模块门禁 | 未运行 | `npm.cmd run verify` | 待记录 |
| R1 范围 | 未运行 | `npm.cmd run check:round-scope` | 待记录 |
| 父仓范围 | 未运行 | `npm.cmd run check:parent-scope -- --base 8deeebb85d2db1b7f1b3564fca984503ce5787a2` | 待记录 |
| 独立拆分 | 未运行 | `npm.cmd run verify:extraction` | 待记录 |
| 完整差异 | 未运行 | `git diff --check` 与路径审计 | 待记录 |

## 硬门与回退

- 用户明确回复“R1验收通过，可以创建PR”前不 push、不创建 PR。
- 批准后任何源码、合同、样例、lockfile、脚本或文档变化都会使批准失效。
- 主线前进时先报告差异，不擅自 rebase；冲突解决后全量重验并重新人工确认。
- 不重建或复用共享栈；任何未来操作必须先确认时间窗口与共享基线。
- R1 提交可逆序 `git revert`；R1 无数据库、路由、环境变量、Godot 或运行数据需要恢复。
