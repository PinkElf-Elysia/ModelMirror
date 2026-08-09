# R3 验收记录

状态：R3.1 治理与隔离基线已完成并验证；R3.2-R3.6 尚未实施

固定基线：`380c747e62193855c724a947d99a84070ca623ff`

最终 HEAD、split tree 与 archive SHA-256 只记录在仓外交付清单，避免本文自引用。

## 成功定义

- [ ] 全部 R3 变化严格位于模块目录并符合 schema v3 正向 allowlist。
- [ ] R1/R2 冻结路径相对固定基线零差异。
- [ ] Runtime Pack/Receipt 合同、Compiler、独立 Runtime Simulator 与 parity harness 按批次通过。
- [ ] Creator 双执行实验台在合法、拒绝和竞态场景保持原子锁步。
- [ ] 完整 verify、独立拆分、父前端无回归与浏览器人工验收通过。
- [ ] 父源码、Matrix Oasis 占位、配置和共享栈零改动。
- [ ] 用户完成最终人工验收。

## 批次

| 批次 | 目标 | 提交 | 状态 |
| --- | --- | --- | --- |
| R3.1 | 治理、精确 allowlist 与 R1/R2 冻结 | 本提交；SHA 由下一批或仓外交付清单记录 | 已验证 |
| R3.2 | Runtime Pack/Receipt 合同与 Validator | 待提交 | 未开始 |
| R3.3 | 确定性 Compiler 与安全 CLI | 待提交 | 未开始 |
| R3.4 | 独立 Runtime Simulator 与 parity harness | 待提交 | 未开始 |
| R3.5 | Creator 双执行锁步实验台 | 待提交 | 未开始 |
| R3.6 | 拆分、无回归、浏览器与证据收口 | 待提交 | 未开始 |

## R3.1 验收证据

变更严格限于 17 个模块内治理文件：

- 模块根：`AGENTS.md`、`README.md`、`module-boundary.json`、`package.json`、`package-lock.json`。
- 文档：`docs/ARCHITECTURE.md`、`docs/BOUNDARIES.md`、`docs/DEPENDENCIES_AND_LICENSES.md`、`docs/KNOWN_LIMITATIONS.md`、`docs/RUNTIME_PACK_THREAT_MODEL.md`、`docs/adr/0004-r3-runtime-pack-governance.md`、本文。
- 护栏与测试：`scripts/check-round-scope.mjs`、`scripts/lib/boundary-core.mjs`、`scripts/lib/parent-scope-core.mjs`、`scripts/lib/scope-policy.mjs`、`tests/round-scope.test.mjs`。

已执行并通过：

- `npm.cmd ci --offline --no-audit --no-fund`：从本机缓存安装 79 packages，退出 0；未联网，lockfile 无额外变化。
- 定向 scope/boundary 测试：83/83 通过。
- `npm.cmd test`：221/221 通过。
- `npm.cmd run verify`：7/7 步通过；Creator 构建转换 227 modules；loopback smoke 返回 HTTP 200 并命中 R0/R2 稳定标识。
- `npm.cmd run check:round-scope`：17/17 路径通过。
- `npm.cmd run check:parent-scope -- --base 380c747e62193855c724a947d99a84070ca623ff`：17/17 路径通过。
- `npm.cmd run check:boundary`：checked 87、tracked 84、零违规。
- `npm.cmd prefix` 与 `npm.cmd ls --all`：退出 0。
- `git diff --check`：退出 0。

范围证据：R1/R2 冻结路径相对固定基线零差异；`client`、`server`、`.github`、Docker、根 manifest/lock 与现有 Matrix Oasis 文件零差异；无 staged 文件。

环境事实：首次测试因隔离 worktree 尚无 `node_modules` 出现 6 个 `ERR_MODULE_NOT_FOUND`，执行离线 `npm ci` 后消除。首次 Creator build 在沙箱内创建忽略的 `dist` 目录时遇到 `EPERM`；仅对模块本地 build/verify 使用提升后的文件写权限重跑即通过。未终止进程、未操作父仓或共享栈，故该事件不作为代码失败。

风险与回退：R3.1 仅改变治理、范围护栏、文档和模块根版本标识；逆序 `git revert` 本提交即可恢复 R2 治理。无数据库、服务、路由、环境变量或运行数据迁移。

用户明确回复“R3 验收通过，可以创建PR”前不 push、不创建 PR。
