# R10 验收记录

状态：R10.1 已验证，等待本地提交。

固定基线：`09f4cca4f1e02fe275ada17535597437cac3778d`

## 批次

- [x] R10.1 治理迁移（本批提交；SHA在R10.2记录）
- [ ] R10.2 Marble环境Pipeline
- [ ] R10.3 自动组装与缓存导入
- [ ] R10.4 本地宿主与审批状态机
- [ ] R10.5 Creator与Godot一键预览
- [ ] R10.6 真实资格与验收收口

## R10.1 证据

- 从`origin/main@09f4cca4f1e02fe275ada17535597437cac3778d`创建独立分支`codex/matrix-oasis-r10-prototype-builder`与worktree`C:\tmp\modelmirror-matrix-oasis-r10`；该主线包含R9合并提交。
- schema v10、active round、固定BASE、两个新workspace与R10 Godot前缀、精确Creator/host/test文件allowlist已同步；R1–R9与未知模块路径继续fail-closed。
- R10固定Marble `marble-1.1`、panorama/collider上限、loopback宿主、两道审批和不持久化prompt；只有三个精确provider adapter可联网。
- `npm.cmd ci --offline --ignore-scripts --no-audit --no-fund`安装110个锁定包；`npm.cmd prefix`与`npm.cmd ls --all`退出0。
- scope/boundary正反测试137/137通过；真实round/parent scope均checked=20/changed=20，boundary checked=931/tracked=926，`git diff --check`通过。
- 首次完整verify只因未注入`GODOT_BIN`在doctor前置按预期阻断；使用R4–R9已核验的仓外Godot 4.6.3 console executable后原命令15/15通过，Node 606/606、Godot R4–R7、Creator 247 modules build与HTTP smoke全绿。
- 本批未调用真实模型、Meshy或Marble，未读取供应商凭据，未启动父服务、Docker或共享栈。

## 回退

本轮六批均可按逆序`git revert`。Git回退不删除仓外run、真实供应商资产或远程Marble world。
