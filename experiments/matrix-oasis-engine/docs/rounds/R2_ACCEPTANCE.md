# R2 验收记录

状态：R2.1 治理批次已验证，等待本地提交

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
| R2.1 | 治理、正向 allowlist 与 R1 冻结 | 本批次提交，SHA 在后续批次记录 | 已验证 |
| R2.2 | 浏览器兼容的确定性参考模拟器 | 待记录 | 未开始 |
| R2.3 | 运行语义与夹具轨迹 | 待记录 | 未开始 |
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

## 回退

批次使用逆序 `git revert`；合并后可整体 revert R2 PR。R2 不产生数据库、父路由、环境变量、共享服务或运行数据恢复工作。
