# R7 验收记录

状态：R7.3 已验证，等待本地提交；R7.4–R7.6 未开始。

固定基线：`a4a2a68d2fc5cf056c741cd3101fcf36a250ad6e`
分支：`codex/matrix-oasis-r7-scene-binding`
模块版本：`0.7.0-r7`

## 批次

- [x] R7.1 治理与冻结迁移
- [x] R7.2 Scene Pack contracts/validator
- [x] R7.3 Kenney GLB 与 Godot loader
- [ ] R7.4 Runtime 场景组合
- [ ] R7.5 自动验证与 Splat 资格
- [ ] R7.6 standalone 与人工验收收口

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

## 外部调用事实

R7 不调用 Marble/Meshy，不读取凭据、额度或任务状态。gdgs 仅允许固定 commit 的仓外副本资格验证，不 vendoring。

## 回退

每批逆序 `git revert <sha>`。整体回退六个 R7 提交恢复固定 R6 基线；无数据库、路由、服务、共享栈或运行数据需要恢复。
