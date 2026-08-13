# R12验收记录

状态：R12实施中；初版声明门关闭

固定基线：`6a88c648f3db2afc39574a57066a14c341c161f9`

## 批次

- [x] R12.1 治理与初版声明门（已验证，等待本地提交；SHA在R12.2记录）
- [ ] R12.2 通用候选验收与修复
- [ ] R12.3 六资产组装与宿主扩容
- [ ] R12.4 Marble SPZ自动空间化
- [ ] R12.5 离线端到端与泛化复验
- [ ] R12.6 末班地铁真实资格链
- [ ] R12.7 验收与初版收口

## R12.1证据

- PR #194已合并，`origin/main=6a88c648f3db2afc39574a57066a14c341c161f9`；已确认R11最终提交是该主线祖先且模块树零差异。从该BASE创建独立`codex/matrix-oasis-r12-last-train-mvp`和`C:\tmp\modelmirror-matrix-oasis-r12`，未从R11功能分支stack。
- 本批23个模块内路径；schema v12、active R12、固定BASE、五个兼容扩展package前缀及精确R12文件allowlist已同步。R0–R11验收、ADR、examples、既有Runtime/Creator/Godot/vendor与父仓继续fail-closed。
- 模块版本迁移为`0.12.0-r12`，lock只同步根版本两处；离线、禁脚本`npm.cmd ci --offline --ignore-scripts --no-audit --no-fund`安装120个锁定包，`npm.cmd prefix`与`npm.cmd ls --all --depth=0`退出0。
- `node --test tests/round-scope.test.mjs`为77/77；boundary正负fixture和新增MVP声明门4/4通过。`check:round-scope`与`check:parent-scope -- --base 6a88c648...`均为checked=23/changed=23，`check:boundary`为checked=1078/tracked=1070，`git diff --check`通过。
- 新增`docs/MVP_STATUS.json`及`check:mvp-claim`，当前固定`pending-r12-qualification / claimAllowed=false`；测试证明旧R10完成叙事、状态漂移和缺失R12验收证据均会fail closed。公开状态文档已改为只有R12全部硬门通过后才能声明初版完成。
- 最新树完整`npm.cmd run verify`为20/20步骤，Node 691/691；Godot 4.6.3的R4–R11 import/runtime/parity/3D/Scene/splat、Creator 248 modules build与HTTP smoke全部通过。
- 本批未调用模型、Marble或Meshy，未读取供应商凭据，未生成或提交真实资产，未启动父服务、Docker或共享栈。回退为单独revert R12.1提交，不影响仓外缓存或远程任务。

## 最终硬门

只有模型与环境/资产两批真实调用分别获批，并完成三结局、循环、重置、300帧性能、正式人物/道具/碰撞、窄屏和中性泛化人工验收后，才能将状态改为`R12验收通过`、把机器状态改为`r12-qualified`并记录`MATRIX_OASIS_R12_MVP_READY`。

最终HEAD、split tree、source archive和真实资格artifact hash只记录在仓外交付清单，避免仓内自引用。
