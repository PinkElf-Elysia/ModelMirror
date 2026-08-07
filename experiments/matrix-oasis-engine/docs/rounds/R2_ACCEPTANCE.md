# R2 验收记录

状态：R2.3 模拟语义已验证，等待本地提交

固定基线：`a8e627e217c8c9e2cb8cca83fea8542c47edaeba`

最终 HEAD、split tree 与 archive SHA-256 只记录在仓外交付清单，避免本文自引用。

## 成功定义

- [ ] 全部 R2 变更位于 `experiments/matrix-oasis-engine/**`。
- [ ] R1 contracts、validator、examples 与 R0/R1 验收记录相对固定基线零差异。
- [ ] 参考模拟器完整执行 R1 condition、effects、Cue 与 typed target 语义，并保持确定性与原子回滚。
- [ ] Creator 可在无父服务时运行两个内置夹具和本地合法 JSON，非法候选不会破坏当前会话。
- [ ] `npm run verify`、固定范围检查与历史保留型拆分全部通过。
- [ ] 父源码、Matrix Oasis 页面、配置与共享栈零改动。
- [ ] 用户完成最终人工验收。

## 批次

| 批次 | 目标 | 提交 | 状态 |
| --- | --- | --- | --- |
| R2.1 | 治理、正向 allowlist 与 R1 冻结 | `1b259b7` | 已完成 |
| R2.2 | 浏览器兼容的确定性参考模拟器 | `590449a` | 已完成 |
| R2.3 | 运行语义与夹具轨迹 | 本批次提交，SHA 在后续批次记录 | 已验证 |
| R2.4 | Creator 最小运行实验台 | 待记录 | 未开始 |
| R2.5 | 拆分证据与人工验收包 | 待记录 | 未开始 |

## 固定边界

- 父项目交互、网络、Godot、二进制与秘密策略保持不变。
- R2 不实现 Compiler、Runtime Pack、存档、批量 replay、AI、3D、父接入、Docker、CI 或部署。
- 不重建或复用共享栈；任何例外必须先确认时间窗口与共享基线。
- 用户明确回复“R2验收通过，可以创建PR”前不 push、不创建 PR。

## 证据模板

每批记录目标、精确 diff、命令与退出码、测试数量、提交 SHA、风险和回退。R2.5 补齐工具版本、依赖/许可证、浏览器验收、父仓无回归、standalone tree、archive SHA-256、未运行项及原因。

### R2.1 验证摘要

- `npm.cmd ci --no-audit --no-fund`：退出 0，安装 78 个包；仅保留既有 `esbuild@0.27.7` install-script warning。
- `npm.cmd prefix`：退出 0，指向模块根。
- `npm.cmd ls --all`：退出 0，无 missing 或 extraneous。
- `npm.cmd run check:round-scope`：退出 0，正向 allowlist 与冻结路径检查通过。
- `npm.cmd run check:parent-scope -- --base a8e627e217c8c9e2cb8cca83fea8542c47edaeba`：退出 0，父仓范围检查通过。
- `npm.cmd run verify`：退出 0，167 项测试通过；Creator 构建与 loopback 冒烟通过；Godot 缺失如实报告为非阻塞 warning。
- R1 contracts、validator、examples 与 R0/R1 验收记录相对固定基线零差异。

### R2.2 验证摘要

- 新增 private/UNLICENSED 的 `@matrix-oasis/game-pack-simulator@0.1.0-r2`；只依赖冻结的内部 Validator，不增加第三方依赖。
- `npm.cmd ci --no-audit --no-fund`：退出 0，安装 79 个包；仅保留既有 `esbuild@0.27.7` install-script warning。
- `npm.cmd ls --all`：退出 0，simulator workspace 链接与内部依赖完整，无 missing 或 extraneous。
- `npm.cmd run verify:simulator`：退出 0，12 项核心模拟器测试通过。
- `npm.cmd run verify`：退出 0，179 项测试通过；Creator 构建与 loopback 冒烟继续通过。
- `npm.cmd run check:boundary` 与 `npm.cmd run check:round-scope`：退出 0；模拟器无父依赖、网络、持久化或题材专属实现。
- R1 冻结路径相对 `1b259b7` 零差异；本批不修改 Creator。

### R2.3 验证摘要

- 以题材中性的 mechanics conformance 夹具固定五步权威轨迹，精确断言变量、位置、步数、transition 与 Cue 顺序；同一输入完整执行 20 次，序列化结果逐字节一致。
- 九种 condition、三种 effect 与两种 typed target 均有执行证据；比较符严格/含等号边界、`all`/`any`/`not` 短路、连续 `set`/`add`、正负安全整数边界与中间溢出原子回滚均已单独锁定。
- 创建、effect、node entry 与 ending 的多 Cue 声明顺序及重复发射已锁定；循环、停滞、ending、未知/不可用 action 与精确 step limit 行为均已覆盖。
- Snapshot 门覆盖根与嵌套字段、Pack 身份、状态/位置耦合、步数范围、变量缺失/额外/错型、JSON round-trip 与无敏感值诊断；inspection、failure 与 public result 均保持冻结。
- 末班地铁薄型夹具无需题材特判即可到达三个 ending 并验证显式循环；模拟器运行源码无 examples 导入或题材专属 ID、文案与关键词。
- `npm.cmd run verify:simulator`：退出 0，29 项模拟器核心与语义测试通过。
- `npm.cmd run verify`：退出 0，196 项测试通过；Creator 构建与 loopback 冒烟继续通过，Godot 缺失仍如实报告为非阻塞 warning。
- `npm.cmd run check:boundary`、`npm.cmd run check:round-scope` 与 `git diff --check`：退出 0；R1 冻结路径和 Creator 相对 `590449a` 零差异。
- 本批仅补齐参考模拟语义和测试，不实现 Compiler、Runtime Pack、存档或题材功能；内部 condition test seam 未通过 package root、类型声明或 exports map 暴露。
- 回退本批提交后，R2.2 的公开模拟器核心仍可独立使用。

## 回退

批次使用逆序 `git revert`；合并后可整体 revert R2 PR。R2 不产生数据库、父路由、环境变量、共享服务或运行数据恢复工作。
