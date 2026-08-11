# R8验收记录

状态：R8.1治理已验证，等待本地提交。

固定基线：`21cbbb8b943b6f9d9799f014c44a6349e6124a63`

## 批次

- [x] R8.1 治理与关键路径护栏
- [ ] R8.2 Generation Proposal与Scene Blueprint合同
- [ ] R8.3 OpenAI兼容模型适配器
- [ ] R8.4 生成编排、修复循环与CLI
- [ ] R8.5 真实模型资格验证
- [ ] R8.6 standalone与验收收口

每批在验证后提交；后续批次记录前一提交SHA。真实模型输出、最终HEAD、split tree和archive hash只记录在仓外交付清单，避免自引用或提交供应商内容。

## R8.1证据

- 从 `origin/main@21cbbb8b943b6f9d9799f014c44a6349e6124a63` 创建独立分支/worktree；该基线包含已合并R7，原主工作区未修改。
- 本批仅迁移schema v8、精确allowlist、广义冻结根、关键路径文档和唯一Provider网络例外；未修改apps、examples、既有packages、Godot、资产或vendor。
- `npm.cmd ci --offline --no-audit --no-fund`：86 packages，退出0；无新registry依赖。
- `node --test tests/round-scope.test.mjs tests/boundary.test.mjs`：116/116通过；覆盖四类Git状态、父仓、冻结根、精确Provider路径和网络旁路负测。
- `npm.cmd run check:round-scope`：通过，checked/changed均为21；`check:parent-scope`与固定BASE通过。
- `npm.cmd run check:boundary`：通过，checked=877、tracked=871。
- 注入仓外Godot 4.6.3后 `npm.cmd run verify`：13/13步骤通过；Node 484/484、Godot R4–R7、Creator build与HTTP smoke无回归。
- 未启动父服务、Docker或共享栈，未调用真实模型、Marble或Meshy。

本批提交SHA由R8.2记录。单独revert本提交恢复完整R7治理和模块版本，不影响冻结运行链。
