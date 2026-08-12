# R9 验收记录

状态：R9.1 已验证，等待本地提交；功能、真实Meshy资格和图形验收均未开始。

固定基线：`da5fd0fe39234807ae3c4a1d543b9fd64de66d97`

## 批次

- [x] R9.1 治理与供应商边界
- [ ] R9.2 Prototype Asset Bundle合同
- [ ] R9.3 Meshy Text-to-3D适配器
- [ ] R9.4 GLB规范化与事务发布
- [ ] R9.5 真实Meshy资格验证
- [ ] R9.6 Godot验证与验收收口

## R9.1 证据

- 从 `origin/main@da5fd0fe39234807ae3c4a1d543b9fd64de66d97` 创建独立 `codex/matrix-oasis-r9-asset-materialization` 与 `C:\tmp\modelmirror-matrix-oasis-r9`。该SHA取代计划中的旧BASE；刷新时主线新增PR #159相关提交，与模块路径交集为零，已向用户报告并获确认。
- schema v9、`activeRound=R9`、固定BASE、两个新workspace前缀和精确文件allowlist已同步到机器策略与CLI。R1–R8、Creator、Godot、examples、Kenney资产、vendor和历史验收均在广义冻结根中；正反scope测试65/65通过。
- 网络门仅保留冻结R8 OpenAI adapter，并精确新增未来Meshy adapter路径；同路径正例、helper旁路、环境变量和endpoint策略篡改均有fixture。单并发boundary测试71/71通过；真实 `check:boundary` 为checked=902、tracked=897。
- module与lock根版本迁移到 `0.9.0-r9`。本批未引入registry依赖；Sharp/libvips LGPL-3.0-or-later人工例外仅记录为R9.4待按实际lock精确化的dev-only离线工具边界，不允许vendoring或产品分发。
- `npm.cmd ci --offline --no-audit --no-fund`安装既有88个包，只有既有`esbuild@0.27.7` install-script提示；`npm.cmd prefix`正确，`npm.cmd ls --all`无missing/extraneous（仅平台optional缺失）。
- `check:round-scope`与固定BASE的`check:parent-scope`均为checked/changed=21；`git diff --check`通过。
- 注入已核验仓外Godot `4.6.3` 后，提权仅用于 `C:\tmp` 一次性fixture的完整 `npm.cmd run verify` 14/14通过：Node 561/561、Godot R4–R7全门、Creator 247 modules build和HTTP smoke均通过。
- 首次非提权verify在冻结R5 adapter创建 `C:\tmp` fixture时被沙箱EPERM拒绝；同一原命令获临时目录权限后通过，未修改或放宽冻结代码。一次并发运行round/boundary的诊断命令产生长时间Git fixture拥塞；单文件单并发复跑与最终原始verify均通过。
- 未调用Meshy、Marble或任何真实模型，未读取供应商凭据；未启动Docker、父服务、共享栈或Godot图形预览。本提交可单独revert以恢复完整R8治理与版本。

后续批次记录前一提交SHA。最终HEAD、split tree、archive、真实资产hash和仓外截图只进入交付清单，避免文档自引用或提交供应商产物。
