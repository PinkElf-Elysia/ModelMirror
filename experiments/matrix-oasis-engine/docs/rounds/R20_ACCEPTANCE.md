# R20 确定性 NPC 实体桥验收记录

状态：R20.7已收口；中性与末班地铁隔离预览均由用户判定“基本通过”

测试专用冻结例外：用户在最终门阶段明确批准修复冻结 R8 loopback 超时测试的墙钟竞态。除范围策略、负向护栏和本验收记录外，唯一被解冻的历史测试文件是 `tests/prototype-generation-cli.test.mjs`；没有解冻任何生产文件。测试改为在 loopback 服务端完整收到唯一请求后通过受控 `AbortController` 触发终止，并分别证明 20 ms 配置值传入 timeout seam、同一 signal 传给唯一一次原生 fetch、一次服务端请求、受控 signal 进入 aborted 且该次 loopback fetch 失败关闭、零重试。该测试不宣称验证操作系统的 20 ms 墙钟精度、120秒真实等待、DNS/TLS/connect阶段取消、跨平台调度精度或服务端取消语义；源码护栏继续锁定生产默认 `AbortSignal.timeout` 和 120 秒上限。`packages/prototype-generator/src/openai-compatible.mjs` 生产实现保持冻结，邻近测试文件继续失败关闭。

## 固定基线与边界

- `R20_BASE_SHA=1ef7b86e4c9d5ab57b5e83fc9e0cadccff14375a`
- 分支：`codex/matrix-oasis-r20-deterministic-npc-bridge`
- 版本：`0.20.0-r20`
- R16 Creator 默认预览、R19 合同和既有供应商适配器保持兼容；外部模型、Marble、Meshy、Docker和共享栈请求：0。
- 本轮只证明固定策略 NPC 可通过单写者、R19 Runtime 权威和隔离 Godot 桥行动；不宣称具备 AI 认知、对话、人格、记忆、关系、动画或动态事件。

## 七批已完成交付

| 批次 | 本地提交 | 结果 |
|---|---|---|
| R20.1 治理与二次核查 | `f79f2a8d` | 完成 |
| R20.2 行为与实体合同 | `a6dc9598` | 完成 |
| R20.3 权威增量会话 | `faa1542d` | 完成 |
| R20.4 确定性调度 | `615be985` | 完成 |
| R20.5 Godot 实体桥 | `9ee83c8c` | 完成 |
| R20.6 双缓存资格与证伪 | `5e4f8c09` | 自动资格与针对性证伪通过 |
| R20.7 人工验收与状态收口 | 本批提交 | 两案基本通过；第二版声明门保持关闭 |

## PR 前针对性证伪与修复

- 资格 current 现在绑定 Node 传递依赖、锁文件、外部包入口和实际复制的完整 `apps/runtime-godot` 静态树；R14/R20 任一嵌套脚本或场景漂移都会使旧资格失效，`.godot` 运行缓存不参与身份。
- 修复 capture 发布竞争：rename 前先完整验证 staging；pre-effect 冲突不删除竞争者目录，post-effect 异常只在 final 与预验证 manifest 完全一致时恢复为成功。
- 协调器关闭改为 5 秒有界并强制关闭连接；teardown 逐项尝试进程、timeline、server和临时工程清理。writer lease 释放最多重试一次，持续失败返回静态 cleanup diagnostic，不再吞错宣称成功。
- 修复 R20 资格参数越过冻结 R14 loader 导致的 `GODOT_RUNTIME_INPUT_INVALID`：资格模式改为 R20 自有闭合 overlay 文件，不向 R14 用户参数注入未知选项。
- reset 保留创建或恢复时绑定的自定义 step limit；R19旧裁决和R20增量路径对接受、拒绝、精确重复、ID冲突、revision/head/snapshot stale、step limit、ending和整数溢出完成20轮字节等价差分。
- 到达和返回均要求真实3D路径、目标Y、向上地面法线、capsule净空和同一导航domain；300帧性能样本覆盖完整600帧周期，不能丢弃后半段慢帧。
- 固定人体碰撞胶囊不再继承导入资产的非单位缩放或shear；视觉节点重挂载时保留原全局变换。官方Godot探针验证缩放视觉、物理根和胶囊三者边界。
- preview 只接受当前实现、Godot二进制、完整资格证据和R19全量重放均精确匹配的 current；capture 精确绑定12个文件、当前身份和自身脚本身份。

## 两份真实缓存自动资格

### 中性案例

- NPC 根：`C:\tmp\matrix-oasis-r20-neutral-r10-npc`
- capture：`C:\tmp\matrix-oasis-r20-neutral-r10-capture`
- source run：`321ec3513b39202d3ccb1d41e4c8e5e7b734714a91844b85c53ddb9a089db76a-b8a4ec37b162fef2d16ec43402ccfc3c07ce6c2d5bbbacf88f6a435493bac488`
- R16 qualification：`60b63d9a3bd8d36592314ad6c444e8873edd189071a6f5e13664881e8f6c96ad`
- 1个真实角色绑定；current manifest `sha256:b8ec13840a18f976000e9bca14bea869729e5d6501590b25d1a923e4797e03cc`；revision 2。
- 300帧中位109.015 FPS；process log `sha256:848162f77d1de8e2c514ebf37e5733ba36d768c224aea8b13630171d304422c3`。
- `capture-manifest.json`文件SHA-256为`017378f823cf31018613a7bb8f46abf5f562a365a61b8c548deedf5d8e22c4f1`，离线链和当前实现/Godot身份均复验通过。

### 末班地铁案例

- NPC 根：`C:\tmp\matrix-oasis-r20-last-train-r10-npc`
- capture：`C:\tmp\matrix-oasis-r20-last-train-r10-capture`
- source run：`78e50440ebb50dc4121fd6af89fd777fe5d3fb4aeed8137ebcd1d0b5c217c4e1-15fcdfd595a41a50d066e8a637589b394634fbd68c49c6254d99a3fd9b846b47`
- R16 qualification：`fda3dc97079ec02a40f1c0e5df48897e07996b60a8e9a10d57c363d40570c572`
- 3个真实角色绑定；current manifest `sha256:11dd1e1bf39e853e966b5a17a3793ad1e8e1c3ae6e6f53488d3b4761eb2259b2`；revision 6，覆盖ending与两节点loop。
- 300帧中位105.563 FPS；process log `sha256:d61377adfaeeb386bc2abf393536a9e81902405a03f6d9e5c854f214d9d8b14f`。
- `capture-manifest.json`文件SHA-256为`497fb20e4246a800f1dec5608b8453a382a38813ce1c2eae873ec6d5028c3547`，离线链和当前实现/Godot身份均复验通过。

两案共同绑定实现SHA-256 `74e486cde8bab425e40f11fdc5c7c28ec761dddc25cc61903f55d9c6521c6a44`，均只读取既有本地缓存，未读取供应商凭据，未发生外部网络请求或费用。先前 r6、r7、r8和r9证据保留为历史诊断，不计入最终资格。

## 自动退出门

- 锁定工具链：Godot `4.6.3.stable.official.7d41c59c4`；下载归档SHA-256 `e39986a178d585ce7ac198fb8de6ea436366dc0cc00e594810c2e3e104c04b90`；实际资格可执行文件SHA-256 `63b3b2208819714c9677fbfdd8217c5b7dee8ecf5f383502e826bc9e2227ff5a`。
- `npm.cmd ci`：退出0；锁文件未改。npm报告2项既有依赖告警（1 low、1 high）及2项生命周期脚本审批提示；R20未执行`audit fix`或升级依赖。
- `npm.cmd run verify:r20`：退出0并输出`R20_AUTOMATED_GATES_OK`；Godot bridge 29/29、证伪33/33、真实性4/4、capture 14/14通过。
- `npm.cmd run verify:npc-authority-session`：session 3/3、顶层4/4通过；10,000项Ledger增量与完整重建通过。
- `npm.cmd test --workspace @matrix-oasis/npc-authority-runtime`：23/23通过，包含20轮旧/新增量差分。
- 冻结R8超时测试原实现使用真实`AbortSignal.timeout(20)`，独立重复20次得到12次通过、8次在请求到达loopback处理器前终止；首个standalone extraction失败现场保留于`C:\tmp\matrix-oasis-extraction-W0E1VC`。受控signal修复后目标用例独立重复20/20通过，完整`tests/prototype-generation-cli.test.mjs`为30/30通过；该证据只证明20 ms配置透传、同一signal、一次请求、真实终止和零重试，不证明20 ms墙钟精度。
- `tests/round-scope.test.mjs`：87/87通过，分别证明仅精确放行该测试文件、邻近生成测试仍冻结、生产provider实现仍冻结。生产文件相对R20基线的Git blob保持`0c54dff73b58234fe1544f46254c3adfc87d57f1`不变。
- `npm.cmd run verify`：退出0；根测试935/935通过，Creator build/smoke通过，最终输出`VERIFY_OK steps=29`。
- `npm.cmd run check:round-scope`：`ROUND_SCOPE_OK checked=77 changed=68`；`check:boundary`为`BOUNDARY_OK checked=1429 tracked=1429`；`check:parent-scope`为`PARENT_SCOPE_OK checked=77 changed=68`；`git diff --check`通过。
- Context7在核对Godot变换语义时达到月度额度，改用Godot官方文档核对：`Node.reparent(..., true)`保留Node3D全局变换，`Basis.orthonormalized()`移除scale/shear并保留旋转。

父`client`干净checkout的typecheck和build通过；测试801/803通过。两项失败均在`client/src/data/models.refresh.test.ts`：live counted期望500、实际499，inactive集合比fixture多`moonshotai/kimi-k2.5`；R20父范围为零差异，未修改父client。

第六提交的clean-HEAD standalone extraction通过：source HEAD `5e4f8c091f71958733a2d3decff2ec84a6f7110d`，split commit `3448129c8bc57f04272e1006d07cf787f72b0ad0`，split tree `3c85727a6727d4c848f137dbf913251d69ed0222`，1429个文件；归档SHA-256为`0c41dd8f6626a83887de345da3f53a3a863cd06439c93ad295102f68289c9343`。R20.7形成最终clean HEAD后仍需再次运行同一extraction门，最终哈希在PR证据中记录，避免文档自引用改变source身份。

## 人工验收与声明边界

- 2026-08-31，中性r10 current以1个真实角色和已资格manifest `sha256:b8ec13840a18f976000e9bca14bea869729e5d6501590b25d1a923e4797e03cc`启动。用户观察运动较为平滑、无明显问题，并判定基本通过。
- 2026-08-31，末班地铁r10 current以3个真实角色和已资格manifest `sha256:11dd1e1bf39e853e966b5a17a3793ad1e8e1c3ae6e6f53488d3b4761eb2259b2`启动。用户确认该案基本通过。
- 人工结论只覆盖本机Godot 4.6.3 Forward+、既有两份真实缓存和隔离R20观察预览；“基本通过”不外推为跨GPU/跨平台保证，也不证明角色动画、AI认知、对话、人格、记忆、关系或动态事件。
- loop、ending、Runtime镜像、重放和300帧性能由绑定自动证据证明；人工反馈没有逐项复述这些自动指标，因此本文不把自动capture冒充人工观察。
- R20.7只迁移本轮状态与验收记录，不切换R16 Creator默认入口。当前机器状态为`r20-entity-bridge-qualified / claimAllowed=false / blockingRound=R25`；第二版完成声明仍须等待R25。
