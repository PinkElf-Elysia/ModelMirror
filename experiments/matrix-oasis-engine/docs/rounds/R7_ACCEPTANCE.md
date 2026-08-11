# R7 验收记录

状态：R7.6 自动与人工验收已完成，等待本地提交和最终 HEAD 重验。

固定基线：`a4a2a68d2fc5cf056c741cd3101fcf36a250ad6e`
分支：`codex/matrix-oasis-r7-scene-binding`
模块版本：`0.7.0-r7`

## 批次

- [x] R7.1 治理与冻结迁移
- [x] R7.2 Scene Pack contracts/validator
- [x] R7.3 Kenney GLB 与 Godot loader
- [x] R7.4 Runtime 场景组合
- [x] R7.5 自动验证与 Splat 资格
- [x] R7.6 standalone 与人工验收收口

## R7.1 证据

本批只迁移机器边界、版本、治理文档与正反 scope fixture。R1–R6 实现、Creator、既有 examples、`project.godot`、Godot vendor 与父仓路径零差异。

- `node --test tests/round-scope.test.mjs`：53/53 通过；覆盖 committed/staged/unstaged/untracked、R1–R6 冻结、R7 新前缀、父仓拒绝与 standalone。
- `node scripts/check-round-scope.mjs`：`ROUND_SCOPE_OK checked=17 changed=17`。
- `node scripts/check-parent-scope.mjs --base a4a2a68...`：`PARENT_SCOPE_OK checked=17 changed=17`。
- `node scripts/check-boundary.mjs`：`BOUNDARY_OK checked=818 tracked=814`。
- `git diff --check` 通过；全部 17 个变更路径均在模块内，R1–R6 冻结路径与父仓路径零差异。

提交 SHA 在 R7.2 或仓外交付清单记录，避免自引用。单独 revert 本提交恢复完整 R6 治理与版本。

R7.1 提交：`3cadaa6`（`chore: 建立矩阵绿洲 R7 场景绑定边界`）。

## R7.2 证据

本批新增私有 Scene Pack contracts/validator、模块内三文件校验 CLI 与 GLB 二进制预检；未修改冻结的 Runtime/Compiler/Creator/Godot 工程。Scene Pack 使用冻结 R3 canonical profile，Runtime 身份与 Receipt artifact hash 必须一致。

- `npm.cmd ci --ignore-scripts --no-audit --no-fund`：86 packages，模块自己的 lockfile 已登记两个 workspace；未新增 registry 依赖。
- `npm.cmd run test:scene-pack`：12/12 通过；覆盖合同上下限、canonical/identity/typed references、孤立代理项、路径穿越、缺失/替换资产、junction、GLB header/URI/feature gate。
- 注入已核验仓外 Godot 4.6.3 后 `npm.cmd test`：464/464 通过。
- `npm.cmd prefix` 指向模块根，`npm.cmd ls --all` 无 missing/extraneous；两个新包均为 private/UNLICENSED，只复用现有 Ajv/jsonc-parser 与模块内依赖。
- `npm.cmd run check:boundary`：`BOUNDARY_OK checked=837 tracked=818`；`npm.cmd run check:round-scope`：`ROUND_SCOPE_OK checked=40 changed=37`；固定基线 parent scope 同样 `checked=40 changed=37`。
- `git diff --check` 通过；R1–R6 冻结路径、父仓路径和 `apps/runtime-godot/**` 相对本批 HEAD 均零差异。

本批提交 SHA 在 R7.3 或仓外交付清单记录，避免自引用。单独 revert 本提交移除 Scene Pack Node 合同/校验层，不影响 R7.1 治理基线。

R7.2 提交：`ebced26`（`feature: 定义矩阵绿洲 Scene Pack 与离线资产校验`）。

## R7.3 证据

本批原样接入四个固定 Kenney Prototype Kit 1.0 GLB、许可证与来源锁，并新增 Godot 运行时 GLTF loader、静态 collider 和原子 prepared scene。没有调用 Marble/Meshy、没有新增网络依赖或修改冻结的 R4–R6 场景/runtime/controller。

- 用户批准四个 GLB 共用的精确 `Textures/colormap.png` 例外：8,706 bytes，SHA-256 `0d4947d34ff32acf4a359c7f22ca784e057e7e72f622170a9a77b6fc88fdb70e`；其他外部 URI、data URI 与网络 URI 继续拒绝。
- 用户批准精确 `figurine.glb` SHA-256 `ae0ea82089e66215684b0b2f5a162be9f6c71475085c81c3b80e53abd08b6bd8` 的静态占位例外：先验证原始 27 条 animation 声明，再只在内存候选字节中移除 `animations` 后交给 `GLTFDocument`，并断言结果不含 `AnimationPlayer`；其他含 animation 的资产继续拒绝。
- `npm.cmd run verify:vendor`：GdUnit4 与 R6 demo reference 未漂移；Kenney 5 个运行文件共 150,894 bytes，`matrix-oasis.vendor-tree/1` SHA-256 `ebe687657bc1c6eee2914be74208f553c82e2d05e8361aff1b322d0c6efadfdb`。
- `node --test tests/godot-boundary.test.mjs tests/vendor.test.mjs tests/scene-pack-bundle.test.mjs`：28/28 通过；覆盖精确供应链、纹理/动画例外、GLB URI/feature gate 与 Godot 一方源码边界。
- `node --test tests/vendor.test.mjs tests/extraction-contract.test.mjs`：17/17 通过；Git index、`core.autocrlf=true` standalone checkout 与模块 `.gitattributes` 均保持 Kenney 原始许可证 716 bytes 不变。
- 注入已核验 Godot 4.6.3 后 `npm.cmd run test:godot`：通过；Godot loader 成功加载四个固定 GLB，figurine 无 `AnimationPlayer`，collider 使用世界层 1，失败候选不替换旧 prepared scene。
- 注入同一 Godot 后 `npm.cmd test`：466/466 通过，包含冻结 R1–R6、Scene Pack、Godot harness 与新增 R7 loader 回归。
- `npm.cmd run check:godot-boundary`：`GODOT_BOUNDARY_OK checked=27`；`npm.cmd run check:boundary`：`BOUNDARY_OK checked=850 tracked=850`；`npm.cmd run check:round-scope` 与固定基线 parent scope 均为 `checked=64 changed=57`；`git diff --check` 通过。

本批提交 SHA 在 R7.4 或仓外交付清单记录，避免自引用。单独 revert 本提交移除 Kenney 资产、来源锁与 Godot GLTF loader，R7.2 的纯 Node Scene Pack 合同/验证器仍可独立运行。

R7.3 提交：`0862a8d`（`feature: 接入矩阵绿洲本地 GLB 场景资产`）。

## R7.4 证据

本批新增独立 scene lab、数据驱动场景组合器与仓外临时预览入口。它只调用冻结 R5 Runtime 与 R6 controller/terminal 的公开类；没有修改 R1–R6、Creator、examples、`project.godot` 或既有 Godot 主场景。两份冻结 Authoring 样例使用同一通用生成器产生 canonical Scene Pack，Godot 一方源码没有题材 ID 或条件分支。

- `node --test tests/godot-scene-binding.test.mjs tests/godot-scene-preview.test.mjs`：7/7 通过；覆盖双样例 canonical Scene Pack、node 声明顺序、placement 显隐、静态 concave collider、三文件参数与仓外临时根。
- 注入已核验 Godot 4.6.3 后 `npm.cmd run test:godot`：45/45 通过；覆盖 entry binding、node transition、spawn、terminal anchor、ending、reset，以及 Runtime 失败时旧世界、snapshot、玩家和 terminal 引用不变。
- 同一工具链下 `npm.cmd test`：473/473 通过；首次未设置 `GODOT_BIN` 的运行仅由严格 doctor 按预期拒绝，注入固定工具链后的原命令完整重跑通过。
- `npm.cmd run check:boundary`：`BOUNDARY_OK checked=862 tracked=850`；`npm.cmd run check:round-scope`：`ROUND_SCOPE_OK checked=70 changed=69`；`git diff --check` 通过。
- 玩家坐标断言沿用 R6 的物理容差：X/Z 绑定精确到 0.02 m，Y 允许重力结算后的 0.15 m 容差；Runtime trace 与 placement 顺序不使用跨平台浮点 golden。

本批提交 SHA 在 R7.5 或仓外交付清单记录，避免自引用。单独 revert 本提交移除 scene lab、组合器和预览 harness，R7.3 的严格 Scene/GLB loader 仍保持独立可测试。

R7.4 提交：`49b1ef1`（`feature: 接通矩阵绿洲 Runtime 与场景刷新`）。

## R7.5 证据

本批新增 Scene trace runner、跨 Runtime/Scene 差分 harness、固定帧捕获和 gdgs 仓外资格器。正式工程没有新增 addon、SPZ 转换、供应商连接或题材分支；Marble/Meshy 仍为零调用。

- `npm.cmd run verify:godot:scene`：15/15 Node 场景/资格测试、45/45 Godot/GdUnit 回归、7 个 Scene 差分案例共 26 次运行和 2 个双样例 smoke 全部通过。
- 差分覆盖 mechanics 五步、全部九种 condition/三种 effect/两种 target、unknown/unavailable/ended、正负溢出、step limit、末班地铁三 ending 与显式循环；mechanics 的 Runtime + Scene trace 重复 20 次序列化完全一致。
- 注入固定 Godot 4.6.3 后 `npm.cmd test`：481/481 通过；R1–R6、Scene Pack、vendor、边界与全部新增 harness 无回归。
- 固定帧：mechanics 12 帧 960×540，capture manifest SHA-256 `23b32ffda51b747498c5f2cd423e1c70364159495a975a39d276eb646735b0cd`；last-train 12 帧 640×540，manifest SHA-256 `37377ebbce46d2ba76e85f98e72e096dd19a6a4f9daaccd9b03739db16d0f206`。图片与详细 manifest 仅在仓外 `C:\tmp`。
- gdgs 固定提交 `d9de8db86a63e8bf9067c869dcdbd0614922fd1e` 的 import、smoke/backend/raster/collision/lighting 和固定帧 7 项均退出 0；源 tree `af2ca6aae12b8203186341370d09e8a8e811e60d`，测试前后 checkout 干净不变。
- gdgs 资格结论为 `defer`：固定提交实际 `plugin.cfg` 为 `3.3.0`，与批准计划的 `3.2.0-beta` 不一致；该候选不支持 SPZ，未复制进正式工程。完整报告见 `docs/SPLAT_QUALIFICATION.md`，机器日志仅在仓外。
- `node --test tests/round-scope.test.mjs tests/boundary.test.mjs`：113/113 通过；`check:round-scope` 与固定基线 parent scope 均为 `checked=85 changed=78`，模块 boundary 为 `checked=871 tracked=862`，Godot 一方边界检查 34 文件，`git diff --check` 通过。

本批提交 SHA 在 R7.6 或仓外交付清单记录，避免自引用。单独 revert 本提交移除 trace/capture/资格 harness，R7.4 的 scene lab 仍可独立预览与测试。

R7.5 提交：`c4422a9`（`test: 添加矩阵绿洲场景绑定与 Splat 资格验证`）。

## R7.6 证据

本批只收口 standalone、父仓无回归、图形人工验收和交付证据，不修改 Scene Pack、Godot loader/composer、资产、冻结 Runtime、Creator 或历史验收记录。最终 R7.6 提交 SHA、最终 split tree 与 source archive SHA-256 只记录在仓外交付清单，避免文档自引用。

- `npm.cmd ci --no-audit --no-fund`：86 packages；`npm.cmd prefix` 精确指向模块根；`npm.cmd ls --all` 无 missing/extraneous。仅保留既有 `esbuild@0.27.7` allow-scripts 警告。
- 严格 `npm.cmd run doctor:godot`：Node `24.18.0`、npm `11.16.0`、Git `2.51.0`、Godot `4.6.3` 全部 ready。
- 注入固定 Godot 4.6.3 后 `npm.cmd run verify`：13/13 步通过；Node `481/481`，Godot vendor/import、R5 adapter/parity、R6 3D、R7 Scene、Creator build 与 HTTP smoke 全部成功。
- 提交前 `npm.cmd run verify:extraction`：standalone 871 files，全门禁通过并自动清理临时副本；最终 R7.6 HEAD 将在提交后重新拆分验证并把标识写入仓外交付清单。
- `npm.cmd run check:round-scope` 与固定基线 parent scope 均为 `checked=78 changed=78`；模块 boundary 为 `checked=871 tracked=871`；`git diff --check` 通过。
- 父 `client` 在隔离 worktree 执行 clean `npm.cmd ci --no-audit --no-fund && npm.cmd run build`：384 packages、3069 modules、退出 0，`git status --short -- client` 为空；仅有既有大 chunk 与 esbuild allow-scripts 警告。父后端、Docker 与共享栈未运行。
- 用户分别验收 mechanics 与 last-train 独立 Scene Lab：Forward+ 正常、readiness marker 唯一、Kenney 地面/墙/箱子/figurine、玩家碰撞、终端与 placement 刷新、ending、循环、reset 和 HUD 均确认正常；末班地铁三个 ending 与窄窗口口径一并确认。
- 两个预览均从仓外临时 Runtime Pack/Receipt/Scene Pack 启动并正常关闭，控制台无错误；未调用 Marble/Meshy，未读取凭据、额度或远程任务，未访问父 API 或其他网络服务。
- 已知限制保持不变：Scene Pack/Receipt 均不提供来源真实性；figurine 动画只在内存候选中剥离；gdgs/SPZ 继续延后；没有 AI、Marble、Meshy、正式资产管线、存档、父项目接入、导出或部署。

单独 revert 本批只移除 R7 验收证据；逆序 revert R7.6→R7.1 可恢复固定 R6 基线。无数据库、服务、共享栈或持久运行数据需要恢复。

## 外部调用事实

R7 不调用 Marble/Meshy，不读取凭据、额度或任务状态。gdgs 仅允许固定 commit 的仓外副本资格验证，不 vendoring。

## 回退

每批逆序 `git revert <sha>`。整体回退六个 R7 提交恢复固定 R6 基线；无数据库、路由、服务、共享栈或运行数据需要恢复。
