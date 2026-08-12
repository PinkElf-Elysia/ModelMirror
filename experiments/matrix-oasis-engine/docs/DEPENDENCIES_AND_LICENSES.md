# 依赖与许可证策略

## 模块许可状态

- 模块根与所有内部 workspace：`private: true`。
- 模块许可证：`UNLICENSED`。
- 用途：仅内部实验，不发布 npm 包，不授予再分发许可。

`UNLICENSED` 是发布与分发约束，不代表第三方依赖没有各自许可证。每个依赖仍须保留其上游许可义务。

## 依赖准入

无需额外人工审批的许可证仅限：

- MIT
- Apache-2.0
- BSD-2-Clause
- BSD-3-Clause
- ISC

任何其他许可证、双重许可、未知许可或自定义条款必须在引入前提交人工审批。依赖必须使用精确版本并写入模块自己的 lockfile；不得依赖父仓 manifest、lockfile 或 `node_modules`。

## 当前直接依赖

R0.2 已固定以下 Creator 第三方依赖；R3.2-R3.4 只复用 lockfile 中既有的 Ajv 与 jsonc-parser，不新增 registry 依赖：

| 依赖 | 版本 | 许可证 | 用途 |
| --- | --- | --- | --- |
| React | 19.2.7 | MIT | Creator 视图 |
| React DOM | 19.2.7 | MIT | 浏览器渲染 |
| Vite | 7.3.5 | MIT | 构建与 preview |
| @vitejs/plugin-react | 5.2.0 | MIT | React 转换 |
| TypeScript | 5.8.3 | Apache-2.0 | 类型检查 |
| @types/react | 19.2.17 | MIT | React 类型 |
| @types/react-dom | 19.2.3 | MIT | React DOM 类型 |
| Ajv | 8.20.0 | MIT | JSON Schema 2020-12 严格结构验证 |
| jsonc-parser | 3.3.1 | MIT | 严格 JSON 语法树与重复键定位 |

测试与护栏仍使用 Node 24 内置 `node:test`，不引入 Vitest、Testing Library、Tailwind、路由器或 UI 库。R2 模拟器只精确依赖内部 `@matrix-oasis/game-pack-validator@0.1.0-r1`。R3.2 新增无外部依赖的 `@matrix-oasis/runtime-pack-contracts@0.1.0-r3`，以及精确依赖该合同、Ajv 8.20.0 和 jsonc-parser 3.3.1 的 `@matrix-oasis/runtime-pack-validator@0.1.0-r3`。R3.3 Compiler 只精确依赖冻结 R1 Validator 与上述两个 R3 内部包。R3.4 Runtime Simulator 只依赖内部 Runtime Validator；parity harness 只依赖内部 Compiler、冻结 R2 Simulator、Runtime contracts 与 Runtime Simulator。R3.5 Creator 将直接内部依赖由冻结 R2 Simulator 切换为 parity harness，不增加 registry 或许可证表面。验证过程无网络、无代码生成入库、无父仓依赖。

Ajv 8.20.0 的传递依赖已按模块 lockfile 盘点：

| 依赖 | 版本 | 许可证 | 来源 |
| --- | --- | --- | --- |
| fast-deep-equal | 3.1.3 | MIT | Ajv 间接依赖 |
| fast-uri | 3.1.5 | BSD-3-Clause | Ajv 间接依赖 |
| json-schema-traverse | 1.0.0 | MIT | Ajv 间接依赖 |
| require-from-string | 2.0.2 | MIT | Ajv 间接依赖 |

以上均属于既有许可证准入范围，不新增例外。版本变化时必须重新盘点。

当前 `npm audit` 对 Vite 的间接开发依赖 `esbuild@0.27.7` 报告 1 个 low severity 项（`GHSA-g7r4-m6w7-qqqr`，Windows 开发服务器场景）。R3 不自动升级用户锁定的工具链；开发与 preview 只允许绑定 loopback，后续升级前须重新审计和审批。

## 人工批准的许可证例外

| 包 | 版本 | 许可证 | 范围 | 审批状态 |
| --- | --- | --- | --- | --- |
| caniuse-lite | 1.0.30001807 | CC-BY-4.0 | `@vitejs/plugin-react` 经 Babel/Browserslist 引入的间接开发依赖 | 用户于 2026-08-06 在 R0 实施任务中明确批准 |

该例外只适用于上述精确包与版本，不扩展 CC-BY-4.0 的通用准入范围。若分发依赖材料，必须保留上游归因与许可证通知；版本变化后需要重新盘点并审批。

## R4 Godot 工具与第三方源码

- Godot Engine `4.6.3-stable`：MIT，官方 Windows 标准版二进制只放仓外，并以官方 `SHA512-SUMS.txt` 核验；不进入 npm lockfile 或 Git。
- GdUnit4 `v6.2.0` / commit `d18770221c2df4a3c991a42fdce7907df40eea75`：MIT，dev-only 原样 vendoring；来源、归档哈希和目录树哈希由 `third-party/gdunit4.lock.json` 固定。
- GdUnit4 tag 源归档 SHA-256 为 `74e00f49e245b9b0c1599d1359d0ea88d1a867d05d7e5b12fa982bc4ca312a1a`；599 文件原样 addon 的 `matrix-oasis.vendor-tree/1` SHA-256 为 `4b1904e747517348cc05134d45b91e7244c92923fb4b6823e700fa4f255664ab`。
- `@satelliteoflove/godot-mcp@4.1.0` 与 `@ryanmazzolini/minimal-godot-mcp@0.1.6` 仅在仓外一次性副本资格验证，不加入 package.json、lockfile 或正式工程。

模块根版本标识为 `0.4.0-r4`；R3 contracts、Validator、Compiler、Runtime Simulator 与 parity harness workspace 均保持 `0.1.0-r3`；Creator 保持 `0.3.0-r3`，参考模拟器保持 `0.1.0-r2`，Authoring 合同与验证器保持 `0.1.0-r1`。全部 workspace 均为 private/UNLICENSED。GdUnit4 若需要任何补丁或版本切换，必须先取得人工审批。

## 变更流程

## R5 依赖状态

R5 不新增 npm、Godot addon、GDExtension 或其他第三方运行依赖。Godot Runtime Pack 适配器、第三执行器、差分 harness 与调试 HUD 均使用现有 Node 24、Godot 4.6.3、GDScript、内建 `HashingContext` 和已冻结的 GdUnit4；R4 的 vendored 字节与来源锁保持不变。

任何新增 addon、原生扩展或 vendored 源码都必须先提交依赖变更申请并取得人工批准，不能混入 R5 功能批次。

## R6 官方参考源码

R6 不新增运行依赖或 Godot addon。仅按已批准方案保存 `godotengine/godot-demo-projects` commit `b4eff8de9d7ba5a4f1a2dea8bae60f28816b7eea` 的 `3d/kinematic_character/player/cubio.gd` 作为非可执行参考，并保留仓库 MIT License、源文件 SHA-256、来源锁和适配说明。正式控制器是独立第一方实现，参考文件不被 Godot 导入、加载或执行。

该参考只用于 CharacterBody3D 重力、相机方向移动、加减速和重置插值模式；不引入社区 FPS 插件、状态机、镜头特效或资产。任何来源 commit、文件字节、许可证或用途变化均需重新人工审批。

## R7 场景资产与资格候选

- Kenney Prototype Kit 1.0：CC0-1.0。只 vendoring `floor-square.glb`、`wall.glb`、`crate.glb`、`figurine.glb` 四个固定文件、它们共同引用的精确 `Textures/colormap.png`（8,706 bytes，SHA-256 `0d4947d34ff32acf4a359c7f22ca784e057e7e72f622170a9a77b6fc88fdb70e`）以及许可证/来源锁；不复制整包。
- 精确 `figurine.glb`（SHA-256 `ae0ea82089e66215684b0b2f5a162be9f6c71475085c81c3b80e53abd08b6bd8`）含 27 条上游 animation 声明，仅作为静态角色占位：原始字节先通过供应链/GLB 门禁，再在内存候选中移除 `animations` 后交给 Godot，并断言不产生 `AnimationPlayer`。此例外不扩展到任何其他资产。
- `ReconWorldLab/godot-gaussian-splatting` commit `d9de8db86a63e8bf9067c869dcdbd0614922fd1e`：MIT，仅在仓外副本资格验证，不加入正式工程、package lock 或 Godot addon。
- R7 不增加 registry 依赖；Scene contracts/validator 只复用模块内 Runtime contracts、Ajv 与 jsonc-parser。
- Marble/Meshy 均不作为依赖，R7 不调用其 API/MCP、不读取凭据或下载生成物。

## R8 原型生成合同

R8.2 新增私有 `@matrix-oasis/prototype-generation-contracts@0.1.0-r8`。该包只精确依赖冻结的 Authoring contracts/validator、Runtime contracts，以及 lockfile 中既有的 Ajv `8.20.0` 与 jsonc-parser `3.3.1`；没有新增 registry 包或许可证例外。合同验证与 canonical 输出完全离线，不调用模型、资产供应商或其他网络服务。

R8.3 新增私有 `@matrix-oasis/prototype-generator@0.1.0-r8`。R8.4 为同一包增加冻结的内部 Compiler、Runtime contracts 与 Runtime simulator 精确依赖，用于生成后的编译、Receipt canonical 化和初始会话门禁；全部为模块内 workspace。OpenAI兼容适配器使用Node 24原生`fetch`、`AbortSignal`、`TextEncoder`和`TextDecoder`，CLI事务使用Node内建文件API，不增加模型SDK、HTTP库、文件事务库或其他registry依赖。

## R9 资产工具链审批

R9.1 仅记录依赖决策，尚未引入新的 registry 包。R9.4 计划精确锁定 `@gltf-transform/core@4.4.2`、`@gltf-transform/extensions@4.4.2`、`@gltf-transform/functions@4.4.2`、`meshoptimizer@1.2.0` 与 `sharp@0.35.3`；必须以实际 lockfile 为准逐项复核传递依赖、平台可选包和许可证。

用户已批准 Sharp/libvips 家族的 LGPL-3.0-or-later 例外，严格限于模块本地、离线、dev-only 的GLB规范化工具链。不得 vendoring libvips 二进制，不得将其打入 Creator、Godot、Runtime Pack、Scene Pack或任何产品分发。R9.4 在安装后必须把实际出现的精确包名、版本、许可证和dev/optional作用域写入机器策略；若实际lock超出本范围，必须重新停报审批。

R9.2 新增私有 `@matrix-oasis/prototype-asset-contracts@0.1.0-r9`。该包只依赖内部 `@matrix-oasis/runtime-pack-contracts@0.1.0-r3`，并复用 lockfile 中已有的 Ajv `8.20.0`（MIT）与 jsonc-parser `3.3.1`（MIT）；其传递依赖版本与上表一致。本批没有新增 registry tarball、平台二进制、install script 或许可证例外。

新增或升级依赖时必须：

1. 记录精确版本、直接/间接用途和许可证；
2. 更新模块 lockfile；
3. 运行 `npm ci`、`npm ls --all` 和完整验证；
4. 重新执行拆分验证；
5. 若许可证不在准入清单，先取得人工批准。
