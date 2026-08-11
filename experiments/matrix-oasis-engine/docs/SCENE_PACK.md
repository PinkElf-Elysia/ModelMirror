# Scene Pack 0.1.0

Scene Pack 是与 Runtime Pack 并列的只读本地 sidecar。它只描述 3D 资产、placement、节点可见性、玩家出生点与 Action 终端锚点，不改变 Runtime Pack、Receipt、snapshot 或执行语义。

## 固定入口

```text
npm.cmd run validate:scene-pack -- \
  --scene <模块内相对路径> \
  --runtime-pack <模块内相对路径> \
  --runtime-receipt <模块内相对路径>
```

三份输入都必须是模块内普通文件；拒绝绝对路径、`..`、symlink/junction、非法 UTF-8 与读取期间替换。Scene manifest 最大 256 KiB，Runtime Pack 最大 16 MiB，Receipt 最大 16 KiB。

独立 Godot 场景实验台可从两个冻结样例生成仓外临时 bundle，并只把三份本地只读文件交给新 scene lab：

```text
npm.cmd run preview:godot:scene -- --example mechanics-conformance
npm.cmd run preview:godot:scene -- --example last-train-r1
```

场景组合按 Runtime node 切换 placement 显隐、玩家出生点与 Action 终端锚点。候选 Scene Pack、GLB、Runtime 会话或组合任一步失败时，旧世界、会话、玩家和终端引用保持不变。

## 合同

- `format` 固定为 `matrix-oasis.scene-pack`，版本固定 `0.1.0`。
- `canonicalization` 固定为 `matrix-oasis.canonical-json/1`；文本必须与 canonical bytes 完全相同。
- `runtimeIdentity` 同时绑定 Runtime format/version、Pack id/contentVersion、Authoring canonical SHA-256 与 Runtime artifact SHA-256。
- 资产只接受本地 POSIX 相对 `.glb` 路径；每项记录 byte length 与 SHA-256。
- placement 使用整数毫米、毫度与千分比，避免浮点 canonical 差异。
- 每个 Runtime node 必须且只能有一个 binding；`entityId` 可以为空，Action 不获得专属坐标。

## GLB 门禁

Node 入口在交给 Godot 前先验证 GLB 2.0 header、chunk 与声明长度，并拒绝未经批准的外部 URI、动画、skin、camera、required extension 和复杂度超限。四个固定 Kenney GLB 只允许引用同目录树内已锁长度与 SHA-256 的 `Textures/colormap.png`；精确哈希的 figurine 可在原始字节验证后于内存移除 animation 声明，且 Godot 结果必须无 `AnimationPlayer`。这些是固定供应链例外，不是 Scene Pack 的通用能力。Godot 组合层还会使用 `GLTFDocument` 做独立运行时解析与一致的功能/复杂度门禁。

Scene manifest canonical SHA 与每个本地资产 hash 只证明所读字节的完整性，不是签名，不证明作者、服务商或生成来源。

## 明确不做

R7 不定义 Scene Receipt、Action 专属 placement、在线资产地址、SPZ 转换、编辑器保存、资产上传或 Marble/Meshy 调用。任何供应商真实调用必须另行取得单次人工批准。
