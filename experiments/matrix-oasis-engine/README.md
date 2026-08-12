# 矩阵绿洲独立实验模块

这是模镜仓库中可拆分的独立AI原生3D引擎实验模块。R1–R9已建立严格Pack、确定性Runtime、Godot可玩层、Scene Blueprint和真实Meshy资产；R10完成Marble环境、自动组装和Creator一键预览。

## R10目标

```text
纯自然语言 → Generation Proposal → Marble panorama/collider
→ Meshy prop/character → 确定性 Scene Pack → Creator 启动 Godot
```

- 固定基线：`09f4cca4f1e02fe275ada17535597437cac3778d`。
- Marble固定`marble-1.1`纯文本；panorama作为360°视觉、collider GLB作为碰撞。
- SPZ、HQ mesh人工导出、AI NPC、记忆、动态任务、存档和父产品接入不在R10。
- 普通verify不调用真实供应商；真实资格必须取得内容与费用绑定的当次批准。
- R10通过只宣称初版原型闭环，不宣称环境具备视差或生产级场景质量。

主要命令将在各批落地：`verify:prototype-environment`、`verify:prototype-assembly`、`verify:prototype-host`、`verify:prototype-builder`和`verify:r10`。完整回归仍使用`npm.cmd run verify`。
