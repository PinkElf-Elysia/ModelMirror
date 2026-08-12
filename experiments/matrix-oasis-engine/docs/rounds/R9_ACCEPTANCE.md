# R9 验收记录

状态：R9.5 已验证，等待本地提交；R9.6 Godot 图形验收与最终拆分尚未开始。

固定基线：`da5fd0fe39234807ae3c4a1d543b9fd64de66d97`

## 批次

- [x] R9.1 治理与供应商边界（`ec5f8f012b22601efffb9f7df22e3c3053829739`）
- [x] R9.2 Prototype Asset Bundle 合同（`8d2aa16ffcc86ee2932124b86d37bc32b4a66557`）
- [x] R9.3 Meshy Text-to-3D 适配器（`a3289d01`）
- [x] R9.4 GLB 规范化与事务发布（`55d09816c4c8352489eb160854dc9b8668336372`）
- [x] R9.5 真实 Meshy 资格验证（本批提交；SHA 由 R9.6 记录）
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

## R9.4 证据

- Pipeline 公开面扩展为 `planPrototypeAssets`、`materializePrototypeAssetBundle` 与冻结合同验证入口；规划器同时核对 canonical R8 Proposal、Authoring source hash、Runtime Pack/Receipt 完整性和 Scene/Runtime identity。物化只接受当前进程签发的不可伪造 plan handle。
- GLB 门禁直接检查 GLB 2.0 header/chunk/声明长度、严格 JSON、内嵌 buffer/texture、节点/mesh/surface/triangle 上限，并拒绝外部 URI、animation、skin、camera、light、required/未知扩展和非 triangle primitive。
- 精确锁定 glTF Transform `4.4.2`、meshoptimizer `1.2.0` 与 Sharp `0.35.3`。visual 做 XZ 居中、Y=0 落地、prop 最长轴 1 m、静态人物高度 1.75 m、2K 纹理和 100k triangle 上限；collider 清除材质/纹理并限制 10k triangle。冻结 Kenney 平面地面得到显式支持，但整体零尺寸 bounds 继续拒绝。
- `Prototype Asset Bundle` 与脱敏 report 只发布 canonical JSON 和 `assets/*.glb`；不含 prompt、供应商任务 ID、URL、原始响应或凭据。同一 Kenney 原始 GLB 20 次规范化字节一致，输入字节不变。
- 仓外发布使用同父 staging、`wx+` FileHandle、bigint dev/ino、逐阶段 realpath/identity、句柄回读、单次目录 rename 和发布后身份复核；已有目标不覆盖，并发同名发布只有一个成功，换身被静态失败。无法确认安全身份的 staging 保留在 `C:\tmp`，不递归删除不可信路径。
- 精确许可证例外已机器化：14 个 Sharp/libvips `dev=true, optional=true` 平台包沿用用户批准的 LGPL 复合许可范围；`tslib@2.8.1 / 0BSD` 经用户于 2026-08-11 单独批准为 dev-only optional transitive helper。两项均未扩展通用白名单，且不得进入 Creator、Godot 或分发物。
- `npm.cmd ci --no-audit --no-fund` 从 lock 安装 110 包，`npm.cmd prefix` 与 `npm.cmd ls --all` 退出 0；仅报告既有 `esbuild@0.27.7` allow-scripts 提示。
- R9 资产合同/管线定向测试 34/34，覆盖 GLB 负向门、20 次确定性、视觉/碰撞指标、issued-plan/Map hostile surface、真实 `C:\tmp` 发布、并发、existing target 与 post-rename identity 替换。
- 显式使用已核验 Godot 4.6.3 后完整 `npm.cmd test` 595/595 通过；boundary checked=922/tracked=915，round scope 在文档落盘后 checked=54/changed=42，固定 BASE 的 parent scope 与 `git diff --check` 通过。
- 最终树上的完整 `npm.cmd run verify` 15/15 通过：strict doctor、范围/边界、R4–R7 Godot、R1–R9 全部 595 项 Node 测试、Creator 247 modules build 与 HTTP smoke 全绿。R9.4 自动验证未调用 Meshy、Marble 或任何真实供应商，未读取凭据，未启动 Docker、父服务或共享栈。

## R9.5 证据

- 用户逐阶段批准了 `asset-prop` 与 `asset-character` 各自的 preview create、bounded poll、preview download、refine create、bounded poll 与 refine download；每个远程阶段只执行一次，未自动重试或跨阶段复用批准。两项 preview 各消耗 20 credits，两项 refine 各消耗 10 credits，合计 60 credits。
- 上传内容只来自冻结 R8 资格产物中的两个中性 asset brief，不包含用户数据。凭据只由仓外 `C:\tmp\matrix-oasis-r9-secrets\set-r9-meshy-env.ps1` 注入；任务 ID、下载 URL、原始 HTTP 响应与 API Key 未进入仓库、终端交付摘要或生成 Bundle。
- 两份 refine GLB 只保存在仓外 `C:\tmp\matrix-oasis-r9-qualification-meshy-20260811\acquired`：`asset-prop.glb` 为 4,881,524 bytes、SHA-256 `e0f85d1ff8bcd3116858d39ba2a77d41a50f4b16f8fed14c0506299acda82647`；`asset-character.glb` 为 3,992,444 bytes、SHA-256 `dbe9cb67edca84f1e3535e93eddf40bcd63cdc856ca28f738dbcab4eac3e9855`。两者 GLB magic 与 128 MiB 下载上限核验通过。
- 首次真实物化暴露并关闭两个集成缺口：资格输出的 `acquired` 是 `C:\tmp` 下的嵌套真实目录，而 CLI 原先只接受直接子目录；现逐级验证所有祖先的 lstat、bigint identity 与 realpath containment，仍拒绝 symlink/junction。Meshy prop collider 的非 POSITION 属性使简化器停在 13,630 triangles；现 collider 专用预处理先移除不参与碰撞的 NORMAL/UV 等属性，保持 visual 不变且保持 10k 门限，最终为 9,981 triangles。
- 真实 Bundle 原子发布在仓外 `C:\tmp\matrix-oasis-r9-real-asset-bundle-20260811`，合同验证、Bundle/report canonical 与全部文件 hash 回读一致；Bundle SHA-256 为 `e2c3cb75dc73ec390542b6b41de107a88ce8be8fc9835417d42ae0f01b6d03d3`，六个 GLB 共 5,808,480 bytes。
- 规范化结果：prop visual/collider 分别 49,789/9,981 triangles，character visual/collider 分别 52,048/9,998 triangles；visual 纹理最大 2048×2048，collider 无材质或纹理，prop 归一为约 1 m，character 高度为 1.75 m。两份真实输入的四种输出各重复 20 次，所有对应 SHA-256 唯一。
- 资格与发布回归 27/27 通过，覆盖六阶段独立批准表面、无远端标识回显、乱序/重复阶段拒绝、嵌套 acquired 目录、原子发布、并发、existing target 与换身。
- 最终树上完整 `npm.cmd run verify` 15/15 通过：Node 601/601，strict doctor、R4–R7 Godot、Creator 247 modules build 与 HTTP smoke 全绿；boundary checked=924/tracked=922，round scope checked=50/changed=44。文档证据落盘后另行重跑 round/parent/boundary 与 `git diff --check` 作为提交门。
- 本批没有调用 Marble、语言模型或其他供应商，没有修改 Godot、Creator、R1–R8 冻结文件、父仓或共享栈；仓外远程任务和资产不会随 Git revert 自动删除。

## 回退与后续

R9.2–R9.5 可按逆序独立 revert：先删除资格 harness 与两项真实集成修复，再删除规范化/事务发布与精确 dev dependency，再删除 provider，最后按需删除合同 workspace；不影响 R1–R8 或 R9.1 治理。Git 回退不会删除仓外 Meshy 任务、下载物或规范化 Bundle，需按最终仓外交付清单单独处理。

最终 HEAD、split tree、archive、真实资产 hash 和仓外截图只进入交付清单，避免文档自引用或提交供应商产物。
