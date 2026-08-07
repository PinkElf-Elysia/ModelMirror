# R0 验收记录

状态：实现与自动验证完成，等待用户人工验收

固定基线：`952f8094c38b29baffa5de3a5b0caa94e501f45f`

最终 HEAD：仅在仓外交付清单与交付消息中记录，避免本文自引用。

## 成功定义

- [x] 全部变更位于 `experiments/matrix-oasis-engine/**`。
- [x] Creator 可在模块根独立安装、构建、测试和 preview。
- [x] Doctor 如实报告 Node/npm/Git 与 Godot 状态。
- [x] 边界正向与全部负向用例通过。
- [x] subtree 拆分后从空依赖状态完成相同验证。
- [x] 父仓入口、源码、配置、Docker、CI 与 Matrix Oasis 占位页零差异。
- [x] 父前端基线构建通过。
- [x] 浏览器桌面与移动宽度人工冒烟通过。
- [x] 用户人工验收前没有 push 或 PR。
- [x] 未经用户明确要求，没有删除 R0 worktree 或分支。
- [x] 没有重建或触碰共享栈容器；未来操作须先确认时间窗口和共享基线。
- [ ] 用户完成最终人工验收。

## 批次提交

| 批次 | 目标 | 提交 SHA | 结果 |
| --- | --- | --- | --- |
| R0.1 | 治理与隔离契约 | `a02d3da` | 通过 |
| R0.2 | Creator 独立空壳 | `18a4e75` | 通过 |
| R0.3 | Doctor 与边界护栏 | `b8b4870` | 通过 |
| R0.4 | 历史保留型拆分 | `1dbcb6f` | 通过 |
| R0.5 | 证据收口 | 本文不自引用 | 等待人工验收 |

## 文件树摘要

- 根：私有 npm workspace、独立 lockfile、边界策略与局部协作规则。
- `apps/creator-web`：React + TypeScript + Vite 的唯一可运行空壳。
- `scripts`：Doctor、边界、父仓范围、Creator 冒烟、聚合验证与拆分验证。
- `tests`：Creator 契约、Doctor、边界负向 fixtures、父仓范围与拆分状态解析。
- `docs`：架构、ADR、边界、依赖许可证、限制、父项目变更模板与本验收记录。
- 报告写入前的干净树共 36 个受跟踪模块文件；没有 `packages/`、Godot 工程或生成产物。

## 验证证据

| 命令 | 退出码 | 通过数量/关键结果 |
| --- | ---: | --- |
| `npm.cmd ci` | 0 | 新装 70 个包、审计 72 个包；lockfile 可独立安装 |
| `npm.cmd prefix` | 0 | 精确指向模块根 |
| `npm.cmd ls --all --json` | 0 | 无 missing 或 extraneous |
| `npm.cmd run --silent doctor -- --json` | 0 | `ready_with_warnings`；Node 24.18.0、npm 11.16.0、Git 2.51.0 ready，Godot warning |
| `npm.cmd run doctor:godot` | 1（预期） | Godot 4.6.x 未安装，严格未来门按设计失败 |
| `npm.cmd run check:boundary` | 0 | 36 个文件、36 个受跟踪文件，零违规 |
| `npm.cmd test` | 0 | 83/83 通过，含 53 个边界负向子夹具 |
| `npm.cmd run verify` | 0 | doctor → boundary → tests → Creator build → Creator smoke，5/5 步通过 |
| `npm.cmd run check:parent-scope -- --base 952f8094c38b29baffa5de3a5b0caa94e501f45f` | 0 | 固定 BASE 匹配；36 个差异路径全部在模块前缀内 |
| `npm.cmd run verify:extraction` | 0 | standalone `npm ci`、完整 verify 与额外 smoke 全通过 |
| 父 `client`：`npm.cmd ci` | 0 | 基线安装通过；未修改父 lockfile 或源码 |
| 父 `client`：`npm.cmd run build` | 0 | 3,043 个模块完成生产构建 |
| `git diff --check` | 0 | 无空白错误 |

## 拆分证据

以下为本报告写入前的干净实现快照；包含最终验收报告的最终 HEAD、split tree 与 archive 哈希记录在仓外验收清单和交付消息中。

- Split commit：`b7da12f3f97094d567f9baa480379c1bd7549f05`
- Split tree SHA：`f578e52960364e978b3d3cb0350ddcd354d2ee6c`
- Source archive SHA-256：`f86b3a7f0a5768b555950559422898f57ebbfcee3d211a385120a672ef048c04`
- Standalone 文件数：36
- 临时产物：验证成功后由路径护栏精确清理；未遗留临时仓或 archive

## 浏览器验收

- 验收目标：模块生产构建的独立 loopback preview，不启动父前端、父后端或 Docker。
- 桌面：1440 × 900；稳定标题、R0 marker 和四项能力状态均正确；页面级横向溢出为 false。
- 移动：390 × 844；页面级横向溢出为 false；状态表只在自身可聚焦区域内横向滚动。
- 控制台：桌面与移动均为 0 条 warning/error。
- 资源：只观察到 `127.0.0.1` 上的构建 JS、CSS 与 favicon；没有 `/api`、父服务或外部主机请求。
- 截图：桌面与移动截图只保存于仓外，并在交付消息中提供。

## Godot 状态

- R0 是否需要 Godot：否。
- 普通 Doctor：Godot `warning`，R0 总状态 `ready_with_warnings`，退出 0。
- 严格未来门：`doctor:godot` 返回 1，不伪装成已就绪。
- 后续门槛：Godot 4.6.x，只能通过仓库外 `GODOT_BIN` 或 PATH 提供。

## 未运行项与原因

- 未运行父后端测试：`server/**` 与后端配置零差异；R0 不接入任何 API。
- 未运行 Docker/共享栈：R0 明确禁止触碰；任何未来重建须先由用户确认时间窗口和共享基线。
- 未运行 Godot 工程验证：R0 不创建 Godot 工程，且当前环境没有 Godot 4.6.x。
- 未 push、未创建 PR、未发布或部署：等待用户人工验收硬门。

## 风险与已知限制

见 [`../KNOWN_LIMITATIONS.md`](../KNOWN_LIMITATIONS.md)。

安装证据同时报告模块间接开发依赖 `esbuild@0.27.7` 的 1 个 low severity 审计项（`GHSA-g7r4-m6w7-qqqr`，Windows 开发服务器场景）；没有执行自动升级。R0 只允许 loopback 开发/预览，进入更高轮次前必须重新评估依赖升级。父 `client` 基线安装报告 1 low、2 moderate、1 high，属于固定 BASE 的既有依赖状态，R0 未修改父依赖。

## 回退

1. PR 前按 R0.5 → R0.1 逆序 `git revert <sha>`。
2. 合并后 revert 整个 R0 PR 或五个模块专属提交。
3. 不需要恢复数据库、路由、环境变量、Docker、服务、Godot 资产或运行数据。
4. 回退后确认 `/matrix-oasis` 仍为固定基线中的原占位页。

## 人工验收门

只有用户明确回复“R0 验收通过，可以创建 PR”后才允许 push 和创建 draft PR。批准后若源文件、lockfile、脚本或文档变化，批准立即失效，必须重新完成全量验证与人工确认。

若主线在验收后前进，先报告差异，不擅自 rebase。任何 rebase 或冲突解决都会使原批准失效，必须全量重验并重新取得人工确认。

并行分支冲突优先在本模块独立 preview 验收；验收通过后仍须核对固定基线、当前主线与完整 diff，才可进入 PR 审批。
