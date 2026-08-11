# 矩阵绿洲 AI 原生 3D 游戏引擎（实验模块）

这是模镜仓库中可拆分的独立实验模块。R1–R7 已建立严格数据合同、Compiler、确定性 Runtime、Godot 可玩层和离线 Scene Pack；R8 只增加纯文本原型生成入口。

## R8 当前目标

```text
纯自然语言
→ Generation Proposal
→ Authoring Game Pack + 私有 Scene Blueprint
→ Runtime Pack + Receipt
→ Runtime 初始会话验证
```

- 固定基线：`21cbbb8b943b6f9d9799f014c44a6349e6124a63`。
- 模块版本：`0.8.0-r8`，private/UNLICENSED。
- R1–R7、Creator、Godot、examples 和第三方资产全部冻结。
- R8 不生成 3D 资产、不启动 Godot、不调用 Marble/Meshy。
- 真实模型资格验证不进入普通 `verify`，必须逐次人工批准。

完整自动验证继续使用 `npm.cmd run verify`；R8 独立入口和真实资格命令会在对应批次加入。任何父仓修改、共享栈操作或真实供应商调用都需要单独授权。
