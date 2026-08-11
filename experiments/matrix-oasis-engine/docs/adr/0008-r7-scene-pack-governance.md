# ADR-0008：R7 Scene Pack 与离线资产治理

状态：已接受
日期：2026-08-10

## 决策

R7 使用独立 `matrix-oasis.scene-pack` sidecar，把冻结 Runtime artifact 身份映射到本地 GLB、placement、node spawn 与 action anchor。R1–R6 合同、Runtime snapshot、Creator、Godot Runtime 与 playable 实现保持字节冻结。

Scene Pack 使用既有 `matrix-oasis.canonical-json/1`，空间数据使用毫米、毫度与千分比整数，避免新增浮点 canonical profile。Manifest 不使用独立 Receipt；载入时计算 canonical SHA-256，并与 Runtime artifact SHA-256 共同形成 prepared scene 身份。

R7 只接受本地 GLB 2.0。碰撞必须显式引用 collider asset；Kenney 验证夹具可让 visual 与 collider 指向同一受限静态 GLB。SPZ 与所有网络供应商输入均延后。

## 后果

- 删除 R7 新包、`scene_binding`、R7 tests、Kenney 子集和治理文件即可完整回退。
- Scene Pack 与 Runtime Pack 独立演进；场景更换不改变玩法语义。
- Scene Pack hash 仅证明本地字节完整性，不提供签名、作者或供应商真实性。
- Marble/Meshy 真实调用必须逐次人工批准，且不属于 R7 退出条件。
