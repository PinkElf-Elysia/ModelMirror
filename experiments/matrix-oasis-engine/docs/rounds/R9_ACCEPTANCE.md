# R9 验收记录

状态：R9.3 已验证，等待本地提交；GLB 规范化、真实资格和 Godot 图形验收均未开始。

固定基线：`da5fd0fe39234807ae3c4a1d543b9fd64de66d97`

## 批次

- [x] R9.1 治理与供应商边界（`ec5f8f012b22601efffb9f7df22e3c3053829739`）
- [x] R9.2 Prototype Asset Bundle 合同（`8d2aa16ffcc86ee2932124b86d37bc32b4a66557`）
- [x] R9.3 Meshy Text-to-3D 适配器（本批提交；SHA 由 R9.4 记录）
- [ ] R9.4 GLB 规范化与事务发布
- [ ] R9.5 真实 Meshy 资格验证
- [ ] R9.6 Godot 验证与验收收口

## R9.1 证据

- 从 `origin/main@da5fd0fe39234807ae3c4a1d543b9fd64de66d97` 创建独立分支 `codex/matrix-oasis-r9-asset-materialization` 和 worktree `C:\tmp\modelmirror-matrix-oasis-r9`；该 SHA 经用户批准替代旧计划 BASE，主线新增路径与模块无交集。
- schema v9、active round、固定 BASE、两个新 workspace 前缀和精确文件 allowlist 已同步；R1–R8、Creator、Godot、examples、Kenney、vendor 与历史验收在冻结根内。
- scope 正反测试 65/65、boundary 测试 71/71；真实 round/parent/boundary 门通过。
- `npm.cmd ci --offline --no-audit --no-fund`、prefix、`npm.cmd ls --all` 通过；完整 verify 14/14，Node 561/561，Godot R4–R7 与 Creator build/smoke 通过。
- 未调用 Meshy、Marble 或真实模型，未读取供应商凭据，未启动 Docker、父服务或共享栈。

## R9.2 证据

- 新增私有 `@matrix-oasis/prototype-asset-contracts@0.1.0-r9`，闭合 Schema、TypeScript declarations、canonical 验证器和固定诊断表面已实现。
- Bundle 明确保存 Blueprint/Runtime identity、固定 Kenney environment、按 brief 一一对应的 materialization、GLB 相对路径、roles、profile、hash 和离线指标；不保存 prompt、供应商任务/URL/响应、密钥或布局坐标。
- 定向合同测试 13/13：覆盖 20 次 canonical 稳定性、输入不变与深冻结、parse/schema/semantic/integrity 门、隐私脱敏、身份和引用、0/1/16 文件、路径、safe integer、预算、碰撞 triangle、bounds 与孤立代理项。
- workspace lock 只新增内部 link；Ajv `8.20.0`、jsonc-parser `3.3.1` 和 Runtime contracts 均复用既有精确版本，没有新 registry 包或许可证例外。
- `npm.cmd ci --offline --ignore-scripts --no-audit --no-fund` 安装 89 个既有/内部 workspace 包；`npm.cmd ls --all` 退出 0。
- 最终树上 `npm.cmd test` 574/574 通过。第一次未传 `GODOT_BIN` 时只有冻结 doctor 测试失败；显式使用已核验 Godot 4.6.3 后原命令全绿。
- `check:boundary` 为 checked=909/tracked=902；`check:round-scope` 与固定 BASE 的 `check:parent-scope` 均通过（checked=34/changed=29）。
- 最终树上完整 `npm.cmd run verify` 15/15 通过：strict doctor、round/boundary、R4–R7 Godot、R1–R8 链、R9 合同、574 项 Node 测试、Creator 247 modules build 与 HTTP smoke 全绿。第一次外层工具以 10 分钟结束但遗留子进程仍在运行；精确核对进程树并仅停止该次 verify 的 5 个 PID 后，以 20 分钟上限原命令重跑通过。当前未运行 Meshy、Marble、Godot 图形预览、Docker 或共享栈。

## R9.3 证据

- 新增私有 `@matrix-oasis/prototype-asset-pipeline@0.1.0-r9`；公开面只含固定 Meshy identity、边界常量、静态 operational error 与 `createMeshyTextTo3DProvider`。
- adapter 对齐官方 v2 Text-to-3D：固定 endpoint、`meshy-6`、preview standard/triangle/remesh/50,000 polygons/仅 GLB，以及 refine 2K/base-color only/关闭 PBR/remove-lighting/仅 GLB。每个方法最多一个请求，不使用 SSE、redirect 或自动重试。
- 只有 `src/meshy-provider.mjs` 拥有网络能力。provider 不读取环境变量；HTTPS 仅允许官方 API 与 `assets.meshy.ai`，HTTP 仅允许带端口的 loopback 测试服务；JSON 与原始 GLB 分别限制为 1 MiB 和 128 MiB。
- 状态响应只投影固定公开字段，忽略但不返回新增供应商元数据；错误、响应体、任务错误、凭据和底层异常均不会进入静态 diagnostics。
- loopback provider 测试 9/9、Asset Bundle 与 provider 合并门 22/22；覆盖精确 preview/refine body、pending/success/failure、下载 host/字节上限、timeout、429、redirect、HTTP 错误、畸形/超限响应、无重试和 secret 脱敏。
- lockfile 只新增内部 pipeline workspace link；离线 `npm.cmd ci --ignore-scripts --no-audit --no-fund` 安装 90 个既有/内部 workspace 包，`npm.cmd ls --all` 退出 0，没有新增 registry 依赖或许可证例外。
- `check:boundary` 为 checked=915/tracked=909；`check:round-scope` 与固定 BASE 的 `check:parent-scope` 均通过（最终精确计数见提交前复验）。
- 首次全量 `npm.cmd test` 在未传 `GODOT_BIN` 时为 582/583，唯一失败是冻结 doctor CLI 的 Godot 环境前置；显式使用已核验 Godot 4.6.3 后原命令 583/583 全部通过。
- 最终树上完整 `npm.cmd run verify` 15/15 通过：strict doctor、范围/边界、R4–R7 Godot、R1–R9 全部 Node 测试、Creator 247 modules build 与 HTTP smoke 全绿。
- 自动验证只启动 loopback 假服务；没有调用 Meshy、Marble 或其他真实供应商，没有读取凭据，也没有启动 Docker、父服务或共享栈。

## 回退与后续

R9.2 与 R9.3 可按逆序独立 revert：先删除 provider workspace、根测试/lock 接线与本批文档，再按需删除合同 workspace；不影响 R1–R8 或 R9.1 治理。R9.4 只能消费冻结的本合同与 provider；若需要改变 Schema、诊断或传输协议，必须停报并单独申请。

最终 HEAD、split tree、archive、真实资产 hash 和仓外截图只进入交付清单，避免文档自引用或提交供应商产物。
